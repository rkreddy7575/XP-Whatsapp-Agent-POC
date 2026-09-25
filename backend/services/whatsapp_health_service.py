"""
WhatsApp API & Webhook Health Service.

Provides active diagnostic probing of WhatsApp Cloud API credentials and infrastructure:
1. Meta API token validity & expiration detection
2. Phone number registration & messaging tier status
3. Webhook signature verification readiness
4. Operational observability: 24h delivery rates, failure tracking, and recent activity.

Zero credential leakage: secret tokens are never returned in public or owner responses.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

from services.audit_service import audit_service

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


class WhatsAppHealthService:
    """Active diagnostic probe for WhatsApp Business Cloud API integration."""

    def __init__(self):
        self._cached_health: Optional[Dict[str, Any]] = None
        self._cache_time: Optional[datetime] = None
        self._cache_ttl_seconds = 30

    async def check_whatsapp_health(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Actively probes Meta Graph API to verify credentials and phone number status.
        Caches result for 30s to prevent spamming Graph API under frequent dashboard polls.
        """
        now = datetime.now(timezone.utc)
        if not force_refresh and self._cached_health and self._cache_time:
            age = (now - self._cache_time).total_seconds()
            if age < self._cache_ttl_seconds:
                return self._cached_health

        health_result = await self._probe_meta_api()
        self._cached_health = health_result
        self._cache_time = now
        return health_result

    async def _probe_meta_api(self) -> Dict[str, Any]:
        """Internal worker executing the Graph API diagnostic probe."""
        access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
        phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        app_secret = os.getenv("WHATSAPP_APP_SECRET", "").strip()

        now_iso = datetime.now(timezone.utc).isoformat()

        # Check for missing credentials
        if not access_token or access_token.lower().startswith("replace_") or not phone_number_id:
            return {
                "status": "unhealthy",
                "webhook": {
                    "configured": bool(app_secret),
                    "signature_verification": bool(app_secret and not app_secret.lower().startswith("replace_")),
                },
                "credentials": {
                    "access_token_configured": bool(access_token and not access_token.lower().startswith("replace_")),
                    "phone_number_id_configured": bool(phone_number_id),
                    "phone_number_id": phone_number_id or None,
                    "token_status": "unconfigured",
                    "rotation_required": True,
                    "rotation_instruction": "Configure permanent Meta System User token in WHATSAPP_ACCESS_TOKEN.",
                },
                "meta_api": {
                    "reachable": False,
                    "http_status": None,
                    "error_code": None,
                    "error_subcode": None,
                    "error_type": "ConfigurationMissing",
                    "error_message": "WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID is not configured.",
                    "verified_name": None,
                    "display_phone_number": None,
                    "quality_rating": None,
                },
                "timestamp": now_iso,
            }

        url = f"{GRAPH_API_BASE}/{phone_number_id}"
        params = {"fields": "verified_name,display_phone_number,quality_rating,code_verification_status"}
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(url, params=params, headers=headers)

            http_status = response.status_code
            resp_data = response.json() if response.content else {}

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
                        "token_type": "Bearer",
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
                        "verified_name": resp_data.get("verified_name"),
                        "display_phone_number": resp_data.get("display_phone_number"),
                        "quality_rating": resp_data.get("quality_rating"),
                    },
                    "timestamp": now_iso,
                }

            # Graph API error diagnosis
            error_obj = resp_data.get("error", {})
            code = error_obj.get("code")
            subcode = error_obj.get("error_subcode")
            err_type = error_obj.get("type", "OAuthException")
            err_msg = error_obj.get("message", "Meta API request failed.")

            is_expired = (
                http_status in (400, 401)
                and (code in (190, 102) or subcode in (463, 467) or "expired" in err_msg.lower())
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
        Strictly excludes synthetic test events to ensure production integrity.
        """
        probe = await self.check_whatsapp_health(force_refresh=False)
        metrics = audit_service.get_metrics_summary(hours=24, production_only=True)
        recent_failures = audit_service.get_recent_failures(limit=5, production_only=True)
        last_in = audit_service.get_last_event_by_direction("INBOUND", production_only=True)
        last_out = audit_service.get_last_event_by_direction("OUTBOUND", production_only=True)

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
                "failure_rate_percent": metrics.get("failure_rate_percent"),
            },
            "last_incoming_message": {
                "phone": last_in.get("customer_phone", "") if last_in else "",
                "wamid": last_in.get("wamid", "") if last_in else "",
                "timestamp": last_in.get("created_at", "") if last_in else "",
            } if (last_in and last_in.get("customer_phone") and last_in.get("customer_phone") != "UNKNOWN") else None,
            "last_outbound_message": {
                "phone": last_out.get("customer_phone", "") if last_out else "",
                "wamid": last_out.get("wamid", "") if last_out else "",
                "status": last_out.get("status", "SENT") if last_out else "",
                "timestamp": last_out.get("created_at", "") if last_out else "",
            } if (last_out and last_out.get("customer_phone") and last_out.get("customer_phone") != "UNKNOWN") else None,
            "recent_failures": recent_failures,
            "recent_audit_events": audit_service.get_recent_events(limit=15, production_only=True),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# Singleton instance for production use
whatsapp_health_service = WhatsAppHealthService()
