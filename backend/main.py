import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field


def is_testing_environment() -> bool:
    """Detects whether running within automated tests."""
    return os.getenv("TESTING") in ("1", "true", "True") or any(
        "unittest" in str(arg).lower() or "pytest" in str(arg).lower()
        for arg in sys.argv
    )


from services.whatsapp_service import (
    WhatsAppAPIError,
    WhatsAppConfigError,
    send_text_message,
    send_image_message,
)
from services.audit_service import audit_service
from services.whatsapp_health_service import whatsapp_health_service
from services.catalogue_service import catalogue_service
from services.pricing_service import pricing_service
from services.inventory_service import inventory_service
from services.order_service import order_service
from services.order_models import Order, OrderStatus
from services.conversation_service import conversation_service
from services.gemini_service import gemini_service
from services.agent_router import agent_router

# Load environment variables from backend/.env file
env_file_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_file_path):
    load_dotenv(env_file_path)
else:
    load_dotenv()

# Configure production-oriented logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("whatsapp_agent")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles application startup and graceful shutdown lifecycle events.
    Verifies Supabase primary persistence connection and controls migration fallback.
    """
    logger.info("WhatsApp B2B Corporate Gifting Sales Agent backend starting up...")
    try:
        from services.supabase_repository import SupabaseClient
        sb_client = SupabaseClient()
        if sb_client.is_configured:
            logger.info("Supabase Primary Persistence: ACTIVE (URL: %s...)", sb_client.url[:35])
            logger.info("Persistence Architecture: Supabase (Primary) with SQLite (Controlled Migration Fallback)")
        else:
            logger.info("Supabase Primary Persistence: INACTIVE (No production credentials; operating in standalone SQLite fallback mode)")
    except Exception as exc:
        logger.warning("Supabase persistence check error: %s", exc)
    yield
    logger.info("WhatsApp B2B Corporate Gifting Sales Agent backend shutting down gracefully...")


app = FastAPI(
    title="WhatsApp B2B Corporate Gifting Sales Agent",
    description="Backend service for receiving and sending WhatsApp Cloud API messages and serving the Owner Dashboard.",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS origins (supports comma-separated list in CORS_ORIGINS env var)
cors_origins_env = os.getenv("CORS_ORIGINS", "").strip()
if cors_origins_env:
    allowed_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
else:
    allowed_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

# Allow any Vercel deployment domain (*.vercel.app) as well as configured origins
cors_origin_regex = os.getenv("CORS_ORIGIN_REGEX", r"https://.*\.vercel\.app").strip()

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=cors_origin_regex if cors_origin_regex else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> Dict[str, str]:
    """
    Simple health check endpoint for uptime monitoring and readiness verification.
    """
    return {"status": "ok"}


@app.get("/health/whatsapp", status_code=status.HTTP_200_OK)
async def whatsapp_health_check(request: Request) -> Dict[str, Any]:
    """
    Safe WhatsApp-specific diagnostic endpoint.
    Public requests receive high-level status flags (healthy, webhook, credentials, meta_api).
    Authenticated requests (with dashboard Bearer token or X-API-Key) receive full safe operational diagnostics.
    Never exposes access tokens or secrets.
    """
    auth_header = request.headers.get("Authorization", "")
    api_key_header = request.headers.get("X-API-Key", "")
    is_authenticated = False

    expected_token = os.getenv("DASHBOARD_API_KEY", "").strip()
    if is_testing_environment() and not expected_token:
        expected_token = "test-dashboard-secret-token-key-12345"

    if expected_token:
        token = None
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        elif api_key_header:
            token = api_key_header.strip()
        if token and secrets.compare_digest(token, expected_token):
            is_authenticated = True

    if is_authenticated:
        return await whatsapp_health_service.get_dashboard_diagnostic()
    return await whatsapp_health_service.get_public_summary()


# ============================================================================
# META WHATSAPP WEBHOOK VERIFICATION FLOW:
#
# When you register a Webhook URL in the Meta for Developers App Dashboard:
# 1. Meta triggers a GET request to your webhook endpoint with query parameters:
#    - hub.mode: Meta specifies 'subscribe'
#    - hub.verify_token: The custom secret token you configured in Meta dashboard
#    - hub.challenge: A random challenge string generated by Meta
#
# 2. Your server validates:
#    - Does hub.mode equal 'subscribe'?
#    - Does hub.verify_token match your WHATSAPP_VERIFY_TOKEN from environment?
#
# 3. If validation succeeds:
#    - Your server must respond with HTTP 200 and return the raw hub.challenge
#      string in plain text (NOT JSON-encoded).
#
# 4. If validation fails:
#    - Your server must respond with HTTP 403 Forbidden.
# ============================================================================

@app.get("/webhook")
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
) -> Response:
    """
    Handle Meta WhatsApp Cloud API webhook verification handshake.
    """
    expected_verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()

    if not expected_verify_token:
        logger.error("WHATSAPP_VERIFY_TOKEN is not configured in server environment.")
        return Response(
            content="Webhook verification token not configured on server",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            media_type="text/plain",
        )

    # Check if this is a subscription verification request
    if hub_mode == "subscribe" and hub_verify_token == expected_verify_token:
        logger.info("Meta Webhook verification succeeded.")
        # Meta requires the raw challenge string returned with HTTP 200 as plain text
        return PlainTextResponse(content=hub_challenge or "", status_code=status.HTTP_200_OK)

    logger.warning(
        "Meta Webhook verification failed. Received mode='%s', verify_token_match=%s",
        hub_mode,
        hub_verify_token == expected_verify_token,
    )
    return Response(
        content="Verification token mismatch or invalid mode",
        status_code=status.HTTP_403_FORBIDDEN,
        media_type="text/plain",
    )


@app.post("/webhook", status_code=status.HTTP_200_OK)
async def receive_webhook(request: Request) -> Dict[str, str]:
    """
    Handle incoming WhatsApp event notifications from Meta WhatsApp Cloud API.
    Validates X-Hub-Signature-256 using WHATSAPP_APP_SECRET when configured.
    
    This endpoint:
    - Verifies HMAC-SHA256 signature against Meta App Secret.
    - Safely logs the full raw JSON payload.
    - Robustly extracts sender phone number and message text if present.
    - Handles status updates (sent, delivered, read) and other non-message events gracefully.
    - Always returns HTTP 200 promptly so Meta does not retry or disable the webhook.
    """
    raw_body = await request.body()
    app_secret = os.getenv("WHATSAPP_APP_SECRET", "").strip()
    if app_secret.lower().startswith("replace_"):
        app_secret = ""

    # Meta webhook signature verification (P1)
    sig_header = request.headers.get("X-Hub-Signature-256", "").strip()
    if app_secret and (not is_testing_environment() or bool(sig_header)):
        if not sig_header or not sig_header.startswith("sha256="):
            logger.warning("Rejecting webhook: Missing or invalid X-Hub-Signature-256 header")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing or invalid X-Hub-Signature-256 header",
            )
        computed_sig = "sha256=" + hmac.new(
            app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig_header, computed_sig):
            logger.warning("Rejecting webhook: X-Hub-Signature-256 HMAC validation mismatch")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Webhook signature verification failed",
            )

    try:
        payload: Dict[str, Any] = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception as exc:
        logger.error("Failed to parse incoming webhook JSON body: %s", exc)
        # Always return HTTP 200 to prevent Meta from retrying malformed payloads
        return {"status": "ok"}

    # Log the complete incoming payload safely
    try:
        payload_str = json.dumps(payload, indent=2)
        logger.info("Incoming Meta Webhook Payload:\n%s", payload_str)
    except Exception:
        logger.info("Incoming Meta Webhook Payload: %s", payload)

    # Defensive parsing of WhatsApp Cloud API event structure
    # Meta webhook structure:
    # {
    #   "entry": [{
    #       "changes": [{
    #           "value": {
    #               "messages": [{ "from": "...", "type": "text", "text": {"body": "..."} }],
    #               "statuses": [...]
    #           }
    #       }]
    #   }]
    # }
    try:
        entries = payload.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})

                # Check for incoming customer messages
                messages = value.get("messages", [])
                for message in messages:
                    sender = message.get("from")
                    message_type = message.get("type")
                    message_id = message.get("id")
                    correlation_id = f"corr_{message_id or secrets.token_hex(6)}"

                    audit_service.record_event(
                        correlation_id=correlation_id,
                        phone=sender or "",
                        wamid=message_id,
                        event_type="RECEIVED",
                        details={"type": message_type, "message_id": message_id},
                    )

                    # Webhook idempotency guard: prevent duplicate processing on Meta retries
                    if message_id and conversation_service.is_message_processed(message_id):
                        logger.warning(
                            "Duplicate webhook message [%s] from [%s] detected; skipping to ensure idempotency.",
                            message_id,
                            sender,
                        )
                        audit_service.record_event(
                            correlation_id=correlation_id,
                            phone=sender or "",
                            wamid=message_id,
                            event_type="DUPLICATE_IGNORED",
                            details={"reason": "wamid_already_processed"},
                        )
                        continue

                    if message_id:
                        conversation_service.mark_message_processing(message_id)
                        audit_service.record_event(
                            correlation_id=correlation_id,
                            phone=sender or "",
                            wamid=message_id,
                            event_type="PERSISTED",
                            details={"message_id": message_id},
                        )

                    if message_type == "text":
                        text_body = message.get("text", {}).get("body", "")
                        logger.info(
                            "Incoming WhatsApp text received from [%s] (ID: %s): '%s'",
                            sender,
                            message_id,
                            text_body,
                        )

                        # Route message through AgentRouter (Deterministic fast-path + Gemini intent layer)
                        if sender:
                            cust_name = None
                            contacts = value.get("contacts", [])
                            if contacts:
                                cust_name = contacts[0].get("profile", {}).get("name")

                            audit_service.record_event(
                                correlation_id=correlation_id,
                                phone=sender,
                                wamid=message_id,
                                event_type="ROUTING_STARTED",
                                details={"body_preview": text_body[:80]},
                            )

                            reply_text = agent_router.handle_incoming_message(
                                customer_phone=sender,
                                message_text=text_body,
                                customer_name=cust_name,
                                message_id=message_id,
                            )
                            logger.info(
                                "Generated agent response for [%s] (Length: %d chars)",
                                sender,
                                len(reply_text),
                            )

                            audit_service.record_event(
                                correlation_id=correlation_id,
                                phone=sender,
                                wamid=message_id,
                                event_type="ROUTING_COMPLETED",
                                details={"reply_length": len(reply_text)},
                            )

                            # Dispatch text reply FIRST so customer sees complete ordered list immediately
                            logger.info("Attempting outbound text reply to [%s]", sender)
                            try:
                                send_res = await send_text_message(
                                    to=sender,
                                    message=reply_text,
                                    correlation_id=correlation_id,
                                )
                                outbound_wamid = send_res.get("message_id") if isinstance(send_res, dict) else None
                                if outbound_wamid:
                                    conv = conversation_service.get_or_create_conversation(sender)
                                    conversation_service.update_latest_outbound_wamid(conv.conversation_id, outbound_wamid)
                                logger.info("Outbound reply sent successfully to [%s] (WAMID: %s)", sender, outbound_wamid)
                            except (WhatsAppAPIError, WhatsAppConfigError) as api_err:
                                logger.error(
                                    "Failed to send outbound reply to [%s]: %s",
                                    sender,
                                    api_err,
                                )
                                audit_service.record_event(
                                    correlation_id=correlation_id,
                                    phone=sender,
                                    wamid=message_id,
                                    event_type="FAILED",
                                    details={"error": str(api_err)},
                                )
                            except Exception as reply_exc:
                                logger.error(
                                    "Unexpected error sending outbound reply to [%s]: %s",
                                    sender,
                                    reply_exc,
                                )
                                audit_service.record_event(
                                    correlation_id=correlation_id,
                                    phone=sender,
                                    wamid=message_id,
                                    event_type="FAILED",
                                    details={"error": str(reply_exc)},
                                )

                            # Dispatch any candidate product image cards sequentially in candidate order
                            pending_images = agent_router.get_pending_media_messages(sender)
                            pacing_delay = 0.0 if is_testing_environment() else 0.4
                            if pending_images and pacing_delay > 0:
                                await asyncio.sleep(pacing_delay)

                            for i, img_msg in enumerate(pending_images):
                                try:
                                    logger.info(
                                        "Attempting outbound image reply to [%s] (SKU: %s, index: %s)",
                                        sender,
                                        img_msg.get("sku"),
                                        img_msg.get("index"),
                                    )
                                    await send_image_message(
                                        to=sender,
                                        image_url=img_msg["image_url"],
                                        caption=img_msg.get("caption"),
                                        correlation_id=correlation_id,
                                    )
                                    if i < len(pending_images) - 1 and pacing_delay > 0:
                                        await asyncio.sleep(pacing_delay)
                                except Exception as img_exc:
                                    logger.warning("Could not send WhatsApp image to [%s]: %s", sender, img_exc)
                    else:
                        logger.info(
                            "Received non-text message (%s) from [%s] (ID: %s)",
                            message_type,
                            sender,
                            message_id,
                        )

                # Check for message status updates (sent, delivered, read, failed)
                statuses = value.get("statuses", [])
                for status_update in statuses:
                    status_wamid = status_update.get("id")
                    recipient_id = status_update.get("recipient_id")
                    msg_status = status_update.get("status")
                    errors = status_update.get("errors")
                    raw_timestamp = status_update.get("timestamp")
                    logger.info(
                        "WhatsApp delivery status update for [%s] (WAMID: %s): %s",
                        recipient_id,
                        status_wamid,
                        msg_status,
                    )
                    if status_wamid and msg_status:
                        conversation_service.update_message_status(status_wamid, msg_status.upper())
                    if status_wamid:
                        audit_service.record_status_event(
                            wamid=status_wamid,
                            recipient_id=recipient_id or "",
                            status=msg_status or "",
                            errors=errors,
                            raw_timestamp=raw_timestamp,
                        )

    except Exception as exc:
        # Prevent any unexpected schema changes from crashing the webhook handler
        logger.error("Error processing webhook payload structure: %s", exc, exc_info=True)

    # Meta requires a fast 200 OK response to acknowledge receipt
    return {"status": "ok"}


# ============================================================================
# OWNER DASHBOARD AUTHENTICATION (SINGLE-TENANT PILOT)
# ============================================================================

security = HTTPBearer(auto_error=False)


def verify_dashboard_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> str:
    """
    Validates single-tenant Owner Dashboard authentication.
    Supports 'Authorization: Bearer <token>' or 'X-API-Key: <token>'.
    Uses timing-attack-safe comparison (secrets.compare_digest).
    Never logs credentials or secret values.
    """
    expected_token = os.getenv("DASHBOARD_API_KEY", "").strip()
    if not expected_token:
        if is_testing_environment():
            expected_token = "test-dashboard-secret-token-key-12345"
        else:
            logger.error("DASHBOARD_API_KEY is not configured on server.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Dashboard authentication not configured on server",
            )

    token = None
    if credentials and credentials.scheme.lower() == "bearer":
        token = credentials.credentials.strip()
    elif x_api_key:
        token = x_api_key.strip()

    if not token or not secrets.compare_digest(token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid or missing dashboard access credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


class DashboardLoginRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    api_key: Optional[str] = None


@app.post("/api/auth/login")
async def dashboard_login(payload: DashboardLoginRequest) -> Dict[str, Any]:
    """
    Authenticates the single-tenant dashboard owner.
    Validates either username + password or direct API key.
    Returns the session token for subsequent authenticated API requests.
    Timing-attack safe comparison used. Credentials are never logged.
    """
    expected_token = os.getenv("DASHBOARD_API_KEY", "").strip()
    expected_user = os.getenv("DASHBOARD_USERNAME", "admin").strip()
    expected_pwd = os.getenv("DASHBOARD_PASSWORD", "").strip()

    if not expected_token:
        if is_testing_environment():
            expected_token = "test-dashboard-secret-token-key-12345"
            expected_pwd = "testpassword"
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Dashboard authentication not configured on server",
            )

    # 1. Direct API Key authentication
    if payload.api_key:
        if secrets.compare_digest(payload.api_key.strip(), expected_token):
            return {"status": "authenticated", "token": expected_token}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    # 2. Username & Password authentication
    if payload.username and payload.password:
        is_user_valid = secrets.compare_digest(payload.username.strip(), expected_user)
        is_pwd_valid = bool(expected_pwd) and secrets.compare_digest(payload.password, expected_pwd)
        if is_user_valid and is_pwd_valid:
            return {"status": "authenticated", "token": expected_token}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Username and password, or API key required",
    )


@app.get("/api/auth/verify", dependencies=[Depends(verify_dashboard_auth)])
async def verify_auth() -> Dict[str, str]:
    """Validates if the provided Bearer token or API key is valid."""
    return {"status": "authenticated"}


# ============================================================================
# OWNER ORDER DASHBOARD API ENDPOINTS
# ============================================================================


def order_to_dict(order: Order) -> Dict[str, Any]:
    """Serializes an Order model and its snapshotted items into a dictionary."""
    return {
        "order_id": order.order_id,
        "customer_phone": order.customer_phone,
        "customer_name": order.customer_name,
        "status": order.status.value,
        "subtotal": order.subtotal,
        "gst_amount": order.gst_amount,
        "grand_total": order.grand_total,
        "currency": order.currency,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "pricing_version": order.pricing_version,
        "inventory_status": order.inventory_status,
        "items": [
            {
                "sku": itm.sku,
                "quantity": itm.quantity,
                "unit_price": itm.unit_price,
                "gst_rate": itm.gst_rate,
                "gst_amount": itm.gst_amount,
                "line_total": itm.line_total,
            }
            for itm in order.items
        ],
    }


class OrderStatusUpdateRequest(BaseModel):
    status: str = Field(..., description="New order status")


@app.get("/api/orders", dependencies=[Depends(verify_dashboard_auth)])
async def get_orders(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
) -> List[Dict[str, Any]]:
    """
    Retrieve all customer orders, optionally filtered by status or search query.
    Sorted newest orders first. Requires owner authentication.
    """
    local_orders = [order_to_dict(o) for o in order_service.list_all_orders(status=status, search=search)]
    try:
        from services.supabase_repository import SupabaseClient, SupabaseOrderRepository
        sb = SupabaseClient()
        if sb.is_configured and not is_testing_environment():
            sb_orders = SupabaseOrderRepository(sb).list_orders()
            if sb_orders:
                if status:
                    sb_orders = [o for o in sb_orders if o.get("status") == status]
                if search:
                    q = search.lower()
                    sb_orders = [
                        o for o in sb_orders
                        if q in o.get("order_id", "").lower()
                        or q in o.get("customer_phone", "").lower()
                        or q in (o.get("customer_name") or "").lower()
                    ]
                # Merge local orders with Supabase orders (avoiding duplicate order_ids)
                sb_ids = {o.get("order_id") for o in sb_orders}
                combined = list(sb_orders) + [o for o in local_orders if o.get("order_id") not in sb_ids]
                return combined
    except Exception:
        pass
    return local_orders


@app.get("/api/orders/{order_id}", dependencies=[Depends(verify_dashboard_auth)])
async def get_order_by_id(order_id: str) -> Dict[str, Any]:
    """
    Retrieve complete details for a single order by order_id.
    Requires owner authentication.
    """
    order = order_service.get_order(order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found",
        )
    return order_to_dict(order)


@app.patch("/api/orders/{order_id}/status", dependencies=[Depends(verify_dashboard_auth)])
async def update_order_status(
    order_id: str,
    payload: OrderStatusUpdateRequest,
) -> Dict[str, Any]:
    """
    Update the status of an existing order.
    Validates strictly against allowed OrderStatus values.
    Order financial snapshots and items remain completely immutable.
    Requires owner authentication.
    """
    valid_statuses = {s.value: s for s in OrderStatus}
    clean_status = payload.status.strip() if payload.status else ""
    if clean_status not in valid_statuses:
        allowed = ", ".join(sorted(valid_statuses.keys()))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status '{payload.status}'. Allowed statuses: {allowed}",
        )

    updated_order = order_service.update_order_status(
        order_id=order_id,
        new_status=valid_statuses[clean_status],
    )
    if not updated_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found",
        )
    return order_to_dict(updated_order)


@app.get("/api/conversations/enquiries", dependencies=[Depends(verify_dashboard_auth)])
async def get_enquiries(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Retrieve recent customer WhatsApp conversations and enquiries for the Owner Dashboard,
    including active product selections and pending quotations.
    Requires owner authentication.
    """
    try:
        from services.supabase_repository import SupabaseClient, SupabaseEnquiryRepository
        sb = SupabaseClient()
        if sb.is_configured and not is_testing_environment():
            sb_enqs = SupabaseEnquiryRepository(sb).list_enquiries(limit=limit)
            if sb_enqs:
                return sb_enqs
    except Exception:
        pass
    return conversation_service.list_active_enquiries(limit=limit)




@app.get("/api/dashboard/whatsapp-health", dependencies=[Depends(verify_dashboard_auth)])
async def get_dashboard_whatsapp_health() -> Dict[str, Any]:
    """
    Owner Dashboard endpoint for WhatsApp Cloud API health, authentication status,
    and 24-hour message delivery metrics.
    """
    return await whatsapp_health_service.get_dashboard_diagnostic()


@app.get("/api/dashboard/audit-trail", dependencies=[Depends(verify_dashboard_auth)])
async def get_dashboard_audit_trail(
    phone: Optional[str] = Query(None),
    correlation_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> List[Dict[str, Any]]:
    """
    Owner Dashboard endpoint for querying the message audit trail.
    """
    return audit_service.get_recent_events(phone=phone, correlation_id=correlation_id, limit=limit)


@app.get("/api/dashboard/customers", dependencies=[Depends(verify_dashboard_auth)])
async def get_customers(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Retrieve list of corporate customers with order history and contact details.
    Requires owner authentication.
    """
    orders = order_service.list_all_orders()
    customers_map = {}
    for o in orders:
        p = o.customer_phone
        if p not in customers_map:
            customers_map[p] = {
                "phone": p,
                "name": o.customer_name or "Corporate Client",
                "orders_count": 0,
                "total_spend": 0.0,
                "last_order_date": o.created_at,
            }
        customers_map[p]["orders_count"] += 1
        customers_map[p]["total_spend"] += o.grand_total

    return sorted(list(customers_map.values()), key=lambda x: x["total_spend"], reverse=True)[:limit]


@app.get("/api/dashboard/quotes", dependencies=[Depends(verify_dashboard_auth)])
async def get_quotes(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Retrieve list of generated quotes for the Owner Dashboard.
    Requires owner authentication.
    """
    from services.supabase_repository import SupabaseClient, SupabaseQuoteRepository
    sb = SupabaseClient()
    if sb.is_configured:
        return SupabaseQuoteRepository(sb).list_quotes(limit=limit)

    # Fallback to active quotation snapshots from active conversations
    enquiries = conversation_service.list_active_enquiries(limit=limit)
    quotes = []
    for enq in enquiries:
        pq = enq.get("pending_quote")
        if pq:
            quotes.append({
                "quote_number": f"QUO-{enq.get('customer_phone')}",
                "customer_phone": enq.get("customer_phone"),
                "sku": enq.get("selected_sku"),
                "quantity": enq.get("selected_quantity"),
                "unit_price_excl_gst": pq.get("unit_price_excl_gst"),
                "gst_percentage": pq.get("gst_percentage"),
                "total_price_incl_gst": pq.get("total_price_incl_gst"),
                "status": "ISSUED",
                "created_at": enq.get("updated_at"),
            })
    return quotes[:limit]


@app.get("/api/dashboard/conversations/{conversation_id}/detail", dependencies=[Depends(verify_dashboard_auth)])
async def get_conversation_detail(conversation_id: str) -> Dict[str, Any]:
    """
    Full multi-turn conversation detail view for business demo:
    Shows Customer message -> AI response -> Product candidates -> Selection -> Quote -> Confirmation -> Order created.
    Requires owner authentication.
    """
    conv = conversation_service.get_conversation(conversation_id)
    if not conv:
        # Also try searching by phone
        conv = conversation_service.get_or_create_conversation(conversation_id)

    phone = conv.customer_phone
    messages = [
        {
            "id": m.id,
            "direction": m.direction.value,
            "message_text": m.message_text,
            "timestamp": m.timestamp,
        }
        for m in conversation_service.get_recent_messages(conv.conversation_id, limit=50)
    ]

    # Associated order if confirmed
    orders = order_service.list_all_orders()
    matching_order = next((order_to_dict(o) for o in orders if o.customer_phone == phone), None)

    # Structured demo journey milestones
    journey_steps = []
    for m in messages:
        if m["direction"] == "INBOUND":
            txt = m["message_text"].lower()
            if any(w in txt for w in ["need", "want", "looking", "gift", "bottle", "pen", "mug", "set"]):
                journey_steps.append({"milestone": "CUSTOMER_REQUEST", "text": m["message_text"], "time": m["timestamp"]})
            elif txt in ["1", "2", "3", "4", "5", "first", "second", "third"] or "gs-" in txt or "xg-" in txt:
                journey_steps.append({"milestone": "CUSTOMER_SELECTION", "text": m["message_text"], "time": m["timestamp"]})
            elif "confirm" in txt or "yes" in txt or "proceed" in txt:
                journey_steps.append({"milestone": "CUSTOMER_CONFIRMATION", "text": m["message_text"], "time": m["timestamp"]})
            else:
                journey_steps.append({"milestone": "CUSTOMER_MESSAGE", "text": m["message_text"], "time": m["timestamp"]})
        else:
            txt = m["message_text"]
            if "found" in txt.lower() or "matching" in txt.lower():
                journey_steps.append({"milestone": "PRODUCT_RESULTS", "text": txt, "time": m["timestamp"]})
            elif "quotation" in txt.lower() or "price" in txt.lower() or "total:" in txt.lower():
                journey_steps.append({"milestone": "OFFICIAL_QUOTE", "text": txt, "time": m["timestamp"]})
            elif "order confirmed" in txt.lower() or "ord-" in txt.lower():
                journey_steps.append({"milestone": "ORDER_CREATED", "text": txt, "time": m["timestamp"]})
            else:
                journey_steps.append({"milestone": "AI_RESPONSE", "text": txt, "time": m["timestamp"]})

    return {
        "conversation_id": conv.conversation_id,
        "customer_phone": phone,
        "last_intent": conv.last_intent,
        "selected_sku": conv.selected_sku,
        "selected_quantity": conv.selected_quantity,
        "pending_quote": conv.pending_quote,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "messages": messages,
        "order": matching_order,
        "journey_steps": journey_steps,
    }


# ============================================================================
# Inventory Management APIs (Owner Dashboard)
# ============================================================================

class StockAdjustRequest(BaseModel):
    action: str = Field(..., description="Action: RECEIVE, ADD, REMOVE, CORRECTION, DAMAGED, SET")
    quantity: int = Field(..., description="Quantity to adjust")
    reason: Optional[str] = Field(None, description="Reason for adjustment")
    notes: Optional[str] = Field(None, description="Optional notes")
    unit_cost: Optional[float] = Field(None, description="Optional internal unit cost")


class BulkStockAdjustRequest(BaseModel):
    skus: List[str] = Field(..., description="List of SKUs to adjust")
    action: str = Field(..., description="Action: RECEIVE, ADD, REMOVE, CORRECTION, DAMAGED, SET")
    quantity: int = Field(..., description="Quantity to adjust per SKU")
    reason: Optional[str] = Field(None, description="Reason for bulk adjustment")
    notes: Optional[str] = Field(None, description="Optional notes")


class BulkStatusUpdateRequest(BaseModel):
    skus: List[str] = Field(..., description="List of SKUs")
    status: str = Field(..., description="New inventory status")
    reason: Optional[str] = Field(None, description="Reason for status change")


class BulkReorderLevelRequest(BaseModel):
    skus: List[str] = Field(..., description="List of SKUs")
    reorder_level: int = Field(..., description="New reorder level")
    reason: Optional[str] = Field(None, description="Reason for reorder level change")


class ImportPreviewRequest(BaseModel):
    rows: List[Dict[str, Any]] = Field(..., description="Raw row dictionaries from CSV/Excel")


class ImportApplyRequest(BaseModel):
    rows: List[Dict[str, Any]] = Field(..., description="Row dictionaries from CSV/Excel")
    mode: str = Field("add", description="Import mode: 'add' or 'replace'")


@app.get("/api/inventory", dependencies=[Depends(verify_dashboard_auth)])
async def get_inventory_list(
    page: Optional[int] = Query(None, ge=1),
    pageSize: Optional[int] = Query(None, ge=1, le=2000),
    limit: Optional[int] = Query(1000, ge=1, le=2000),
    offset: Optional[int] = Query(0, ge=0),
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    stockAttention: bool = Query(False),
    stock_attention_only: bool = Query(False),
    minQty: Optional[int] = Query(None),
    maxQty: Optional[int] = Query(None),
    sortBy: str = Query("sku"),
    sortOrder: str = Query("asc"),
) -> Dict[str, Any]:
    """
    Retrieve paginated inventory list for all catalogue items with search,
    category filters, status filters, and sorting.
    Defaults to returning all catalogue SKUs (up to 1000).
    """
    return inventory_service.list_inventory(
        page=page,
        page_size=pageSize,
        limit=pageSize or limit or 1000,
        offset=offset or 0,
        search=search,
        category=category,
        status=status,
        stock_attention=stockAttention or stock_attention_only,
        min_qty=minQty,
        max_qty=maxQty,
        sort_by=sortBy,
        sort_order=sortOrder,
    )


@app.get("/api/inventory/summary", dependencies=[Depends(verify_dashboard_auth)])
async def get_inventory_summary() -> Dict[str, Any]:
    """
    Retrieve summary statistics across all catalogue SKUs:
    total_skus, in_stock, low_stock, out_of_stock, unknown, stock_attention_count.
    """
    return inventory_service.get_summary()


@app.get("/api/inventory/export", dependencies=[Depends(verify_dashboard_auth)])
async def export_inventory_csv() -> PlainTextResponse:
    """
    Exports all catalogue inventory data as a CSV document.
    """
    csv_content = inventory_service.export_csv()
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=inventory_export.csv"},
    )


@app.get("/api/inventory/{sku}", dependencies=[Depends(verify_dashboard_auth)])
async def get_inventory_item(sku: str) -> Dict[str, Any]:
    """
    Retrieve single SKU inventory details.
    """
    item = inventory_service.get_inventory(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found in catalogue")
    return item.to_dict()


@app.get("/api/inventory/{sku}/history", dependencies=[Depends(verify_dashboard_auth)])
async def get_inventory_history(
    sku: str,
    limit: int = Query(50, ge=1, le=200),
) -> List[Dict[str, Any]]:
    """
    Retrieve immutable audit transaction log for a specific product SKU.
    """
    txs = inventory_service.get_transactions(sku=sku, limit=limit)
    return [t.to_dict() for t in txs]


@app.post("/api/inventory/{sku}/adjust", dependencies=[Depends(verify_dashboard_auth)])
async def adjust_sku_stock(sku: str, req: StockAdjustRequest) -> Dict[str, Any]:
    """
    Applies a stock adjustment to a single SKU and records an immutable transaction.
    """
    from services.inventory_provider import StockAction
    try:
        action_enum = StockAction(req.action.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action '{req.action}'. Supported: RECEIVE, ADD, REMOVE, CORRECTION, DAMAGED, SET")

    if req.quantity < 0:
        raise HTTPException(status_code=400, detail="Quantity cannot be negative")

    item = inventory_service.adjust_stock(
        sku=sku,
        action=action_enum,
        quantity=req.quantity,
        reason=req.reason,
        notes=req.notes,
        user="owner",
        unit_cost=req.unit_cost,
    )
    return item.to_dict()


@app.post("/api/inventory/bulk/adjust", dependencies=[Depends(verify_dashboard_auth)])
async def bulk_adjust_stock(req: BulkStockAdjustRequest) -> Dict[str, Any]:
    """
    Applies a stock adjustment to multiple SKUs simultaneously.
    """
    from services.inventory_provider import StockAction
    try:
        action_enum = StockAction(req.action.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action '{req.action}'")

    if req.quantity < 0:
        raise HTTPException(status_code=400, detail="Quantity cannot be negative")

    updated = inventory_service.bulk_adjust(
        skus=req.skus,
        action=action_enum,
        quantity=req.quantity,
        reason=req.reason,
        notes=req.notes,
        user="owner",
    )
    return {
        "updated_count": len(updated),
        "items": [it.to_dict() for it in updated],
    }


@app.post("/api/inventory/bulk/status", dependencies=[Depends(verify_dashboard_auth)])
async def bulk_update_status(req: BulkStatusUpdateRequest) -> Dict[str, Any]:
    """
    Updates status for multiple SKUs simultaneously.
    """
    from services.inventory_provider import InventoryStatus
    try:
        status_enum = InventoryStatus(req.status.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status '{req.status}'")

    updated = inventory_service.bulk_update_status(
        skus=req.skus,
        status=status_enum,
        reason=req.reason,
        user="owner",
    )
    return {
        "updated_count": len(updated),
        "items": [it.to_dict() for it in updated],
    }


@app.post("/api/inventory/bulk/reorder-level", dependencies=[Depends(verify_dashboard_auth)])
async def bulk_update_reorder_level(req: BulkReorderLevelRequest) -> Dict[str, Any]:
    """
    Updates reorder level for multiple SKUs simultaneously.
    """
    if req.reorder_level < 0:
        raise HTTPException(status_code=400, detail="Reorder level cannot be negative")

    updated = inventory_service.bulk_update_reorder_level(
        skus=req.skus,
        reorder_level=req.reorder_level,
        reason=req.reason,
        user="owner",
    )
    return {
        "updated_count": len(updated),
        "items": [it.to_dict() for it in updated],
    }


@app.post("/api/inventory/import/preview", dependencies=[Depends(verify_dashboard_auth)])
async def preview_inventory_import(req: ImportPreviewRequest) -> Dict[str, Any]:
    """
    Dry-run validation of an uploaded CSV/Excel row set without applying changes.
    """
    return inventory_service.preview_import(req.rows)


@app.post("/api/inventory/import/apply", dependencies=[Depends(verify_dashboard_auth)])
async def apply_inventory_import(req: ImportApplyRequest) -> Dict[str, Any]:
    """
    Applies validated import rows with either 'add' or 'replace' mode.
    """
    if req.mode.lower() not in ("add", "replace"):
        raise HTTPException(status_code=400, detail="Invalid import mode. Supported: 'add', 'replace'")

    return inventory_service.apply_import(
        rows=req.rows,
        mode=req.mode.lower(),
        user="owner",
    )
