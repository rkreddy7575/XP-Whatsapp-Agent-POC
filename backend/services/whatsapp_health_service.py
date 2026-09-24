"""
WhatsApp Health and Authentication Service.

Provides deterministic validation of Meta WhatsApp Cloud API credentials,
token expiration detection, and safe operational diagnostics for monitoring
and the Owner Dashboard.

Guarantees zero secret exposure: token values and Authorization headers
are never returned or logged.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import httpx
from dotenv import load_dotenv

from services.audit_service import audit_service

load_dotenv()
logger = logging.getLogger(__name__)


class WhatsAppHealthService:
    def __init__(self):
        self._cached_health: Optional[Dict[str, Any]] = None
        self._cache_timestamp: float = 0.0
        self._cache_ttl_seconds: float = 30.0
        self._lock = asyncio.Lock()

    async def check_whatsapp_health(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Performs a deterministic live health probe of Meta WhatsApp Cloud API credentials.
        Caches results for 30s to avoid exhausting Meta rate limits on repeated monitoring polls.
        """
        now = time.time()
        if not force_refresh and self._cached_health and (now - self._cache_timestamp < self._cache_ttl_seconds):
            return self._cached_health

        async with self._lock:
            # Recheck under lock
            if not force_refresh and self._cached_health and (time.time() - self._cache_timestamp < self._cache_ttl_seconds):
                return self._cached_health

            result = await self._probe_meta_api()
            self._cached_health = result
            self._cache_timestamp = time.time()
            return result

    async def _probe_meta_api(self) -> Dict[str, Any]:
        access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
        phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        graph_api_version = os.getenv("META_GRAPH_API_VERSION", "v19.0").strip()
        app_secret = os.getenv("WHATSAPP_APP_SECRET", "").strip()

        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Config Validation
        if not access_token or not phone_number_id:
            return {
                "status": "unhealthy",
                "webhook": {
                    "configured": bool(app_secret),
                    "signature_verification": bool(app_secret and not app_secret.lower().startswith("replace_")),
                },
                "credentials": {
                    "access_token_configured": bool(access_token),
                    "phone_number_id_configured": bool(phone_number_id),
                    "token_status": "unconfigured",
                    "rotation_required": True,
                    "rotation_instruction": "Configure WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID.",
                },
                "meta_api": {
                    "reachable": False,
                    "http_status": None,
                    "error_code": None,
                    "error_subcode": None,
                    "error_type": None,
                    "error_message": "Missing credentials in environment.",
                    "verified_name": None,
                    "display_phone_number": None,
                    "quality_rating": None,
                },
                "timestamp": now_iso,
            }

        url = (
            f"https://graph.facebook.com/{graph_api_version}/{phone_number_id}"
            "?fields=id,display_phone_number,verified_name,quality_rating,code_verification_status"
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
        }

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(url, headers=headers)

            http_status = response.status_code
            try:
                data = response.json()
            except Exception:
                data = {}

            if response.is_success:
                return {
                    "status": "healthy",
                    "webhook": {
                        "configured": bool(app_secret),
                        "signature_verification": bool(app_secret and not app_secret.lower().startswith("replace_")),
                    },
                    "credentials": {
                        "access_token_configured": True,
                        "phone_number_id_configured": True,
                        "phone_number_id": phone_number_id,
                        "token_status": "valid",
                        "rotation_required": False,
                        "rotation_instruction": None,
                    },
                    "meta_api": {
                        "reachable": True,
                        "http_status": http_status,
                        "error_code": None,
                        "error_subcode": None,
                        "error_type": None,
                        "error_message": None,
                        "verified_name": data.get("verified_name"),
                        "display_phone_number": data.get("display_phone_number"),
                        "quality_rating": data.get("quality_rating"),
                    },
                    "timestamp": now_iso,
                }

            # Failure branch: classify error accurately
            err = data.get("error", {}) if isinstance(data, dict) else {}
            code = err.get("code")
            subcode = err.get("error_subcode")
            err_type = err.get("type", "MetaAPIError")
            err_msg = err.get("message", f"HTTP {http_status} error from Meta Graph API")

            # Check for token expiration or invalidity
            is_expired = (
                http_status == 401
                and (code == 190 or subcode in (463, 467) or "expired" in err_msg.lower() or "session" in err_msg.lower())
            )
            is_invalid = http_status == 401 and not is_expired

            token_status = "expired" if is_expired else ("invalid" if is_invalid else "error")
            rotation_required = is_expired or is_invalid

            instruction = (
                "The configured Meta access token has expired (OAuthException 190). "
                "Generate a fresh permanent System User access token in Meta Business Manager and update WHATSAPP_ACCESS_TOKEN."
                if is_expired
                else (
                    "The configured Meta access token is rejected as invalid by Graph API. Verify WHATSAPP_ACCESS_TOKEN."
                    if is_invalid
                    else None
                )
            )

            logger.warning(
                "WhatsApp API health probe returned status %s (code=%s, subcode=%s, type=%s): %s",
                http_status,
                code,
                subcode,
                err_type,
                err_msg,
            )

            return {
                "status": "unhealthy",
                "webhook": {
                    "configured": bool(app_secret),
                    "signature_verification": bool(app_secret and not app_secret.lower().startswith("replace_")),
                },
                "credentials": {
                    "access_token_configured": True,
                    "phone_number_id_configured": True,
                    "phone_number_id": phone_number_id,
                    "token_status": token_status,
                    "rotation_required": rotation_required,
                    "rotation_instruction": instruction,
                },
                "meta_api": {
                    "reachable": True,
                    "http_status": http_status,
                    "error_code": code,
                    "error_subcode": subcode,
                    "error_type": err_type,
                    "error_message": err_msg,
                    "verified_name": None,
                    "display_phone_number": None,
                    "quality_rating": None,
                },
                "timestamp": now_iso,
            }

        except httpx.RequestError as exc:
            logger.error("WhatsApp API health probe network communication failure: %s", exc)
            return {
                "status": "degraded",
                "webhook": {
                    "configured": bool(app_secret),
                    "signature_verification": bool(app_secret and not app_secret.lower().startswith("replace_")),
                },
                "credentials": {
                    "access_token_configured": True,
                    "phone_number_id_configured": True,
                    "phone_number_id": phone_number_id,
                    "token_status": "unknown",
                    "rotation_required": False,
                    "rotation_instruction": None,
                },
                "meta_api": {
                    "reachable": False,
                    "http_status": None,
                    "error_code": None,
                    "error_subcode": None,
                    "error_type": type(exc).__name__,
                    "error_message": "Network communication failure connecting to Meta Graph API.",
                    "verified_name": None,
                    "display_phone_number": None,
                    "quality_rating": None,
                },
                "timestamp": now_iso,
            }

    async def get_safe_summary(self) -> Dict[str, Any]:
        """
        Returns a concise, safe summary suitable for public monitoring without exposing credentials.
        """
        health = await self.check_whatsapp_health()
        return {
            "status": health.get("status", "unknown"),
            "webhook": "configured" if health.get("webhook", {}).get("configured") else "unconfigured",
            "credentials": health.get("credentials", {}).get("token_status", "unknown"),
            "meta_api": "reachable" if health.get("meta_api", {}).get("reachable") else "unreachable",
            "rotation_required": health.get("credentials", {}).get("rotation_required", False),
            "timestamp": health.get("timestamp"),
        }

    async def get_dashboard_health(self) -> Dict[str, Any]:
        """
        Returns comprehensive health and activity diagnostics for the Owner Dashboard.
        Integrates audit metrics for message volume, failure tracking, and recent activity.
        """
        return await self.get_dashboard_diagnostic()

    async def get_public_summary(self) -> Dict[str, Any]:
        """Alias for get_safe_summary."""
        return await self.get_safe_summary()

    async def get_health_status(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Alias for check_whatsapp_health."""
        return await self.check_whatsapp_health(force_refresh=force_refresh)

    async def get_dashboard_diagnostic(self) -> Dict[str, Any]:
        """
        Returns rich health, authentication, and 24h message delivery metrics for the dashboard.
        """
        probe = await self.check_whatsapp_health(force_refresh=False)
        metrics = audit_service.get_metrics_summary()
        recent_failures = audit_service.get_recent_failures(limit=5)
        last_in = audit_service.get_last_event_by_direction("INBOUND")
        last_out = audit_service.get_last_event_by_direction("OUTBOUND")

        meta_err = None
        if probe.get("meta_api", {}).get("error_code"):
            meta_err = {
                "code": probe["meta_api"]["error_code"],
                "error_subcode": probe["meta_api"]["error_subcode"],
                "type": probe["meta_api"]["error_type"],
                "message": probe["meta_api"]["error_message"],
            }

        return {
            "status": probe.get("status", "unknown"),
            "webhook": "configured" if probe.get("webhook", {}).get("configured") else "unconfigured",
            "credentials": probe.get("credentials", {}).get("token_status", "unknown"),
            "meta_api": "reachable" if probe.get("meta_api", {}).get("reachable") else "unreachable",
            "token_status": probe.get("credentials", {}).get("token_status"),
            "token_type": probe.get("credentials", {}).get("token_type"),
            "phone_number_id": probe.get("credentials", {}).get("phone_number_id"),
            "rotation_required": probe.get("credentials", {}).get("rotation_required", False),
            "rotation_instruction": probe.get("credentials", {}).get("rotation_instruction"),
            "meta_error": meta_err,
            "metrics_24h": {
                "total_received": metrics.get("total_received", 0),
                "total_sent": metrics.get("total_sent", 0),
                "total_delivered": metrics.get("total_delivered", 0),
                "total_failed": metrics.get("total_failed", 0),
                "failure_rate_percent": metrics.get("failure_rate_percent", 0.0),
            },
            "last_incoming_message": {
                "phone": last_in.get("customer_phone", "") if last_in else "",
                "wamid": last_in.get("wamid", "") if last_in else "",
                "timestamp": last_in.get("created_at", "") if last_in else "",
            } if last_in else None,
            "last_outbound_message": {
                "phone": last_out.get("customer_phone", "") if last_out else "",
                "wamid": last_out.get("wamid", "") if last_out else "",
                "status": last_out.get("status", "SENT") if last_out else "",
                "timestamp": last_out.get("created_at", "") if last_out else "",
            } if last_out else None,
            "recent_failures": recent_failures,
            "recent_audit_events": audit_service.get_recent_events(limit=15),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# Singleton instance for production use
whatsapp_health_service = WhatsAppHealthService()
