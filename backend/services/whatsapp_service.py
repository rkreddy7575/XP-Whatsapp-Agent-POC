import logging
import os
from typing import Any, Dict, Optional
import httpx
from dotenv import load_dotenv

# Ensure environment variables are loaded
load_dotenv()

logger = logging.getLogger(__name__)


class WhatsAppConfigError(Exception):
    """Raised when required WhatsApp API configuration is missing."""
    pass


class WhatsAppAPIError(Exception):
    """Raised when the Meta Graph API request fails."""
    pass


def get_whatsapp_config() -> Dict[str, str]:
    """
    Retrieve and validate Meta WhatsApp Cloud API credentials from environment variables.
    Never exposes credentials in error messages.
    """
    access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    graph_api_version = os.getenv("META_GRAPH_API_VERSION", "").strip()

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


async def send_text_message(to: str, message: str) -> Dict[str, Any]:
    """
    Send a WhatsApp text message to a user via Meta's WhatsApp Cloud API.

    Args:
        to: Recipient's phone number in E.164 format without '+' (e.g., '16315551181').
        message: The text content to send.

    Returns:
        Dict containing the Meta API response payload.

    Raises:
        WhatsAppConfigError: If credentials or API version are not configured.
        WhatsAppAPIError: If the Meta Graph API returns an error response.
    """
    config = get_whatsapp_config()

    url = (
        f"https://graph.facebook.com/{config['graph_api_version']}/"
        f"{config['phone_number_id']}/messages"
    )

    headers = {
        "Authorization": f"Bearer {config['access_token']}",
        "Content-Type": "application/json",
    }

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

    logger.info("Sending WhatsApp text message to recipient (length: %d chars)", len(message))

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=headers, json=payload)

        # Parse response safely
        try:
            response_data = response.json()
        except Exception:
            response_data = {"raw_text": response.text}

        if response.is_success:
            logger.info("WhatsApp message successfully dispatched. Status: %s", response.status_code)
            return response_data

        # Safe logging of Meta API error without revealing request authorization header
        error_info = response_data.get("error", {}) if isinstance(response_data, dict) else {}
        error_message = error_info.get("message", "Unknown Meta API error")
        error_type = error_info.get("type", "UnknownType")
        error_code = error_info.get("code", "UnknownCode")

        logger.error(
            "Meta Graph API error (status=%s, code=%s, type=%s): %s",
            response.status_code,
            error_code,
            error_type,
            error_message,
        )

        raise WhatsAppAPIError(
            f"Meta Graph API returned HTTP {response.status_code}: {error_message}"
        )

    except httpx.RequestError as exc:
        logger.error("HTTP network communication error while contacting Meta Graph API: %s", type(exc).__name__)
        raise WhatsAppAPIError(f"Network error communicating with Meta Graph API: {str(exc)}") from exc


async def send_image_message(
    to: str,
    image_url: str,
    caption: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Send a WhatsApp image message to a user via Meta's WhatsApp Cloud API.

    Args:
        to: Recipient's phone number in E.164 format without '+' (e.g., '16315551181').
        image_url: Publicly accessible URL of the image to send.
        caption: Optional text caption accompanying the image.

    Returns:
        Dict containing the Meta API response payload.

    Raises:
        WhatsAppConfigError: If credentials or API version are not configured.
        WhatsAppAPIError: If the Meta Graph API returns an error response.
    """
    config = get_whatsapp_config()

    url = (
        f"https://graph.facebook.com/{config['graph_api_version']}/"
        f"{config['phone_number_id']}/messages"
    )

    headers = {
        "Authorization": f"Bearer {config['access_token']}",
        "Content-Type": "application/json",
    }

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

    logger.info("Sending WhatsApp image message to recipient (url: %s)", image_url)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=headers, json=payload)

        try:
            response_data = response.json()
        except Exception:
            response_data = {"raw_text": response.text}

        if response.is_success:
            logger.info("WhatsApp image message successfully dispatched. Status: %s", response.status_code)
            return response_data

        error_info = response_data.get("error", {}) if isinstance(response_data, dict) else {}
        error_message = error_info.get("message", "Unknown Meta API error")
        error_code = error_info.get("code", "UnknownCode")
        error_type = error_info.get("type", "UnknownType")

        logger.error(
            "Meta Graph API error (status=%s, code=%s, type=%s): %s",
            response.status_code,
            error_code,
            error_type,
            error_message,
        )

        raise WhatsAppAPIError(
            f"Meta Graph API returned HTTP {response.status_code}: {error_message}"
        )

    except httpx.RequestError as exc:
        logger.error("HTTP network communication error while contacting Meta Graph API: %s", type(exc).__name__)
        raise WhatsAppAPIError(f"Network error communicating with Meta Graph API: {str(exc)}") from exc
