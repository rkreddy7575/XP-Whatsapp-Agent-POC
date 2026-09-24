"""
Meta WhatsApp Cloud API Service.

Provides outbound messaging (text, image) with bounded exponential-backoff retries
for transient errors (429 rate limits, 5xx server errors, timeouts).
Guarantees immediate non-retry for authentication errors (401/403) and malformed requests (400).
Integrates with AuditService for complete lifecycle observability.

Never exposes credentials or Authorization headers in logs or exceptions.
"""

import asyncio
import logging
import os
import sys
import uuid
from typing import Any, Dict, Optional
import httpx
from dotenv import load_dotenv

from services.audit_service import audit_service

# Ensure environment variables are loaded
load_dotenv()

logger = logging.getLogger(__name__)


def is_testing_environment() -> bool:
    """Detects whether running within automated tests."""
    return os.getenv("TESTING") in ("1", "true", "True") or any(
        "unittest" in str(arg).lower() or "pytest" in str(arg).lower()
        for arg in sys.argv
    )


class WhatsAppConfigError(Exception):
    """Raised when required WhatsApp API configuration is missing."""
    pass


class WhatsAppAPIError(Exception):
    """Raised when the Meta Graph API request fails."""
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_code: Optional[int] = None,
        error_type: Optional[str] = None,
        is_auth_error: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.error_type = error_type
        self.is_auth_error = is_auth_error


def get_whatsapp_config() -> Dict[str, str]:
    """
    Retrieve and validate Meta WhatsApp Cloud API credentials from environment variables.
    Never exposes credentials in error messages.
    """
    access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    graph_api_version = os.getenv("META_GRAPH_API_VERSION", "v19.0").strip()

    missing = []
    if not access_token:
        missing.append("WHATSAPP_ACCESS_TOKEN")
    if not phone_number_id:
        missing.append("WHATSAPP_PHONE_NUMBER_ID")
    if not graph_api_version:
        missing.append("META_GRAPH_API_VERSION")

    if missing:
        raise WhatsAppConfigError(
            f"Missing required WhatsApp environment configuration: {', '.join(missing)}"
        )

    return {
        "access_token": access_token,
        "phone_number_id": phone_number_id,
        "graph_api_version": graph_api_version,
    }


async def _send_with_retry(
    payload: Dict[str, Any],
    to: str,
    correlation_id: Optional[str] = None,
    media_type: str = "text",
) -> Dict[str, Any]:
    """
    Dispatches a message payload to Meta Graph API with bounded retry logic.

    Retries on:
      - Network timeouts (httpx.TimeoutException)
      - Connection errors (httpx.ConnectError)
      - Rate limiting (HTTP 429)
      - Temporary server errors (HTTP 500, 502, 503, 504)

    DOES NOT retry on:
      - Authentication failures (HTTP 401, 403) -> immediate failure
      - Client bad requests (HTTP 400, 404) -> immediate failure

    Max retries: 2 (total 3 attempts).
    """
    config = get_whatsapp_config()
    clean_to = to.lstrip("+").strip()
    corr_id = correlation_id or f"corr_{uuid.uuid4().hex[:12]}"

    url = (
        f"https://graph.facebook.com/{config['graph_api_version']}/"
        f"{config['phone_number_id']}/messages"
    )

    headers = {
        "Authorization": f"Bearer {config['access_token']}",
        "Content-Type": "application/json",
    }

    max_retries = 2
    delay_base = 0.0 if is_testing_environment() else 0.5
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        audit_service.record_event(
            correlation_id=corr_id,
            customer_phone=clean_to,
            direction="OUTBOUND",
            event_type="SEND_ATTEMPT",
            details={
                "attempt": attempt + 1,
                "max_attempts": max_retries + 1,
                "media_type": media_type,
            },
        )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, headers=headers, json=payload)

            http_status = response.status_code
            try:
                response_data = response.json()
            except Exception:
                response_data = {"raw_text": response.text}

            # 1. Success (HTTP 2xx)
            if response.is_success:
                outbound_wamid = None
                messages_arr = response_data.get("messages", [])
                if messages_arr and isinstance(messages_arr, list):
                    outbound_wamid = messages_arr[0].get("id")

                if isinstance(response_data, dict):
                    response_data["message_id"] = outbound_wamid
                    response_data["status"] = "accepted"

                logger.info(
                    "WhatsApp message successfully dispatched to [%s] (status=%d, wamid=%s, attempt=%d)",
                    clean_to,
                    http_status,
                    outbound_wamid,
                    attempt + 1,
                )

                audit_service.record_event(
                    correlation_id=corr_id,
                    customer_phone=clean_to,
                    direction="OUTBOUND",
                    event_type="META_ACCEPTED",
                    status="SENT",
                    wamid=outbound_wamid,
                    details={
                        "http_status": http_status,
                        "attempt": attempt + 1,
                        "wamid": outbound_wamid,
                    },
                )
                return response_data

            # 2. Extract error info safely (never print authorization headers)
            error_info = response_data.get("error", {}) if isinstance(response_data, dict) else {}
            error_message = error_info.get("message", "Unknown Meta API error")
            error_type = error_info.get("type", "UnknownType")
            error_code = error_info.get("code", "UnknownCode")
            error_subcode = error_info.get("error_subcode")

            is_auth_error = (
                http_status in (401, 403)
                or error_code == 190
            )

            # Permanent failure: Authentication or Client Bad Request -> DO NOT RETRY
            if is_auth_error:
                logger.error(
                    "[AUTH_FAILURE] Meta Graph API authentication error (status=%s, code=%s, type=%s): %s. Token rotation required.",
                    http_status,
                    error_code,
                    error_type,
                    error_message,
                )
                audit_service.record_event(
                    correlation_id=corr_id,
                    customer_phone=clean_to,
                    direction="OUTBOUND",
                    event_type="FAILED",
                    status="FAILED",
                    details={
                        "http_status": http_status,
                        "error_code": error_code,
                        "error_type": error_type,
                        "error_message": error_message,
                        "reason": "AUTHENTICATION_FAILED",
                    },
                )
                raise WhatsAppAPIError(
                    f"Meta Graph API authentication failed (HTTP {http_status}): {error_message}",
                    status_code=http_status,
                    error_code=error_code,
                    error_type=error_type,
                    is_auth_error=True,
                )

            if http_status in (400, 404):
                logger.error(
                    "Meta Graph API permanent client error (status=%s, code=%s, type=%s): %s",
                    http_status,
                    error_code,
                    error_type,
                    error_message,
                )
                audit_service.record_event(
                    correlation_id=corr_id,
                    customer_phone=clean_to,
                    direction="OUTBOUND",
                    event_type="FAILED",
                    status="FAILED",
                    details={
                        "http_status": http_status,
                        "error_code": error_code,
                        "error_type": error_type,
                        "error_message": error_message,
                        "reason": "CLIENT_ERROR",
                    },
                )
                raise WhatsAppAPIError(
                    f"Meta Graph API permanent client error (HTTP {http_status}): {error_message}",
                    status_code=http_status,
                    error_code=error_code,
                    error_type=error_type,
                )

            # Transient failure: Rate Limit (429) or Server Error (5xx) -> RETRY with backoff
            is_transient = http_status == 429 or (500 <= http_status <= 599)
            if is_transient and attempt < max_retries:
                backoff = delay_base * (2 ** attempt)
                logger.warning(
                    "Temporary Meta Graph API error (status=%s, code=%s). Retrying in %.2fs (attempt %d/%d)...",
                    http_status,
                    error_code,
                    backoff,
                    attempt + 1,
                    max_retries + 1,
                )
                if backoff > 0:
                    await asyncio.sleep(backoff)
                elif is_testing_environment():
                    await asyncio.sleep(0)
                continue

            # Out of retries
            logger.error(
                "Meta Graph API error exhausted retries (status=%s, code=%s, type=%s): %s",
                http_status,
                error_code,
                error_type,
                error_message,
            )
            audit_service.record_event(
                correlation_id=corr_id,
                customer_phone=clean_to,
                direction="OUTBOUND",
                event_type="FAILED",
                status="FAILED",
                details={
                    "http_status": http_status,
                    "error_code": error_code,
                    "error_type": error_type,
                    "error_message": error_message,
                    "reason": "EXHAUSTED_RETRIES",
                },
            )
            raise WhatsAppAPIError(
                f"Meta Graph API returned HTTP {http_status}: {error_message}",
                status_code=http_status,
                error_code=error_code,
                error_type=error_type,
            )

        except (httpx.TimeoutException, httpx.ConnectError) as net_err:
            last_error = net_err
            if attempt < max_retries:
                backoff = delay_base * (2 ** attempt)
                logger.warning(
                    "Meta Graph API network timeout/connection error (%s). Retrying in %.2fs (attempt %d/%d)...",
                    type(net_err).__name__,
                    backoff,
                    attempt + 1,
                    max_retries + 1,
                )
                if backoff > 0:
                    await asyncio.sleep(backoff)
                continue

            logger.error("Meta Graph API network failure exhausted retries: %s", type(net_err).__name__)
            audit_service.record_event(
                correlation_id=corr_id,
                customer_phone=clean_to,
                direction="OUTBOUND",
                event_type="FAILED",
                status="FAILED",
                details={
                    "error_type": type(net_err).__name__,
                    "error_message": str(net_err),
                    "reason": "NETWORK_TIMEOUT",
                },
            )
            raise WhatsAppAPIError(
                f"Network error communicating with Meta Graph API: {str(net_err)}"
            ) from net_err

    # Fallback guard
    if last_error:
        raise WhatsAppAPIError(f"Meta Graph API request failed: {last_error}") from last_error
    raise WhatsAppAPIError("Meta Graph API request failed after retries.")


async def send_text_message(
    to: str,
    message: str,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Send a WhatsApp text message to a user via Meta's WhatsApp Cloud API.

    Args:
        to: Recipient's phone number in E.164 format without '+' (e.g., '16315551181').
        message: The text content to send.
        correlation_id: Optional tracking correlation ID for lifecycle tracing.

    Returns:
        Dict containing the Meta API response payload (including messages[0].id).
    """
    clean_to = to.lstrip("+").strip()
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": clean_to,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message,
        },
    }

    logger.info("Sending WhatsApp text message to [%s] (length: %d chars)", clean_to, len(message))
    return await _send_with_retry(payload, to=clean_to, correlation_id=correlation_id, media_type="text")


async def send_image_message(
    to: str,
    image_url: str,
    caption: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Send a WhatsApp image message to a user via Meta's WhatsApp Cloud API.

    Args:
        to: Recipient's phone number in E.164 format without '+' (e.g., '16315551181').
        image_url: Publicly accessible URL of the image to send.
        caption: Optional text caption accompanying the image.
        correlation_id: Optional tracking correlation ID for lifecycle tracing.

    Returns:
        Dict containing the Meta API response payload.
    """
    clean_to = to.lstrip("+").strip()
    image_payload: Dict[str, Any] = {"link": image_url}
    if caption:
        image_payload["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": clean_to,
        "type": "image",
        "image": image_payload,
    }

    logger.info("Sending WhatsApp image message to [%s] (url: %s)", clean_to, image_url)
    return await _send_with_retry(payload, to=clean_to, correlation_id=correlation_id, media_type="image")
