"""
Supabase Repository & Data Access Layer
Provides clean abstractions for all 8 required repositories:
- SupabaseProductRepository
- SupabasePricingRepository
- SupabaseCustomerRepository
- SupabaseConversationRepository
- SupabaseMessageRepository
- SupabaseEnquiryRepository
- SupabaseQuoteRepository
- SupabaseOrderRepository

Configured exclusively via SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.
Never exposes secret keys to client components.
Supports dependency injection / mock client for unit testing without live network connections.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from services.pricing_service import PriceQuoteResult
from services.supabase_models import (
    ProductRecord,
    PricingRuleRecord,
    CustomerRecord,
    ConversationRecord,
    MessageRecord,
    EnquiryRecord,
    QuoteRecord,
    OrderRecord,
    OrderItemRecord,
    OrderStatusHistoryRecord,
    MessageAuditEventRecord,
    normalize_sku,
    clean_sku_key,
)

logger = logging.getLogger("supabase_repository")


class SupabaseClient:
    """
    Low-level PostgREST HTTP client for Supabase PostgreSQL.
    Safely executes REST operations with error handling.
    """

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        service_role_key: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        self.url = (supabase_url or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.key = service_role_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY", "")
        self.client = http_client or httpx.Client(timeout=15.0)

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.key and not self.url.startswith("https://your-project"))

    def _headers(self, prefer: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def select(self, table: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if not self.is_configured:
            return []
        url = f"{self.url}/rest/v1/{table}"
        try:
            resp = self.client.get(url, headers=self._headers(), params=params or {})
            if resp.is_success:
                return resp.json()
            logger.warning("Supabase select %s failed: %d %s", table, resp.status_code, resp.text)
            return []
        except Exception as exc:
            logger.error("Supabase select error on %s: %s", table, exc)
            return []

    def insert(self, table: str, data: Any, on_conflict: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.is_configured:
            return [data] if isinstance(data, dict) else data
        url = f"{self.url}/rest/v1/{table}"
        prefer = "return=representation"
        if on_conflict:
            prefer += f",resolution=merge-duplicates"
        params = {"on_conflict": on_conflict} if on_conflict else {}
        try:
            resp = self.client.post(url, headers=self._headers(prefer=prefer), json=data, params=params)
            if resp.is_success:
                return resp.json()
            logger.warning("Supabase insert %s failed: %d %s", table, resp.status_code, resp.text)
            return []
        except Exception as exc:
            logger.error("Supabase insert error on %s: %s", table, exc)
            return []

    def delete(self, table: str, params: Dict[str, Any]) -> bool:
        if not self.is_configured:
            return False
        url = f"{self.url}/rest/v1/{table}"
        try:
            resp = self.client.delete(url, headers=self._headers(), params=params)
            if resp.is_success:
                return True
            logger.warning("Supabase delete %s failed: %d %s", table, resp.status_code, resp.text)
            return False
        except Exception as exc:
            logger.error("Supabase delete error on %s: %s", table, exc)
            return False

    def update(self, table: str, data: Dict[str, Any], params: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self.is_configured:
            return [data]
        url = f"{self.url}/rest/v1/{table}"
        try:
            resp = self.client.patch(url, headers=self._headers(prefer="return=representation"), json=data, params=params)
            if resp.is_success:
                return resp.json()
            logger.warning("Supabase update %s failed: %d %s", table, resp.status_code, resp.text)
            return []
        except Exception as exc:
            logger.error("Supabase update error on %s: %s", table, exc)
            return []


# =============================================================================
# 1. SupabaseProductRepository
# =============================================================================
class SupabaseProductRepository:
    def __init__(self, client: Optional[SupabaseClient] = None, tenant_id: str = "default"):
        self.client = client or SupabaseClient()
        self.tenant_id = tenant_id

    def get_by_sku(self, sku: str) -> Optional[Dict[str, Any]]:
        clean_sku = normalize_sku(sku)
        clean_key = clean_sku_key(sku)
        # Search exact or normalized
        params = {"tenant_id": f"eq.{self.tenant_id}", "sku": f"eq.{clean_sku}", "limit": "1"}
        res = self.client.select("products", params)
        if res:
            return res[0]
        # Search normalized_sku
        params = {"tenant_id": f"eq.{self.tenant_id}", "normalized_sku": f"eq.{clean_sku}", "limit": "1"}
        res = self.client.select("products", params)
        return res[0] if res else None

    def search_products(
        self,
        query: Optional[str] = None,
        category: Optional[str] = None,
        max_price: Optional[float] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "tenant_id": f"eq.{self.tenant_id}",
            "status": "eq.ACTIVE",
            "limit": str(limit),
        }
        if category:
            params["category"] = f"ilike.%{category}%"
        if query and not category:
            params["or"] = f"(name.ilike.%{query}%,category.ilike.%{query}%,subcategory.ilike.%{query}%,sku.ilike.%{query}%)"
        if max_price:
            params["source_price"] = f"lte.{max_price}"

        return self.client.select("products", params)

    def update_image_url(self, sku: str, image_url: str) -> bool:
        clean_sku = normalize_sku(sku)
        res = self.client.update("products", {"image_url": image_url}, {"tenant_id": f"eq.{self.tenant_id}", "sku": f"eq.{clean_sku}"})
        return bool(res)

    def list_categories(self) -> List[str]:
        res = self.client.select("products", {"tenant_id": f"eq.{self.tenant_id}", "select": "category"})
        return sorted(list({r["category"] for r in res if r.get("category")}))


# =============================================================================
# 2. SupabasePricingRepository
# =============================================================================
class SupabasePricingRepository:
    def __init__(self, client: Optional[SupabaseClient] = None, tenant_id: str = "default"):
        self.client = client or SupabaseClient()
        self.tenant_id = tenant_id

    def get_rules_for_sku(self, sku: str) -> List[Dict[str, Any]]:
        clean_sku = normalize_sku(sku)
        clean_key = clean_sku_key(sku)

        candidates = [clean_sku]
        if clean_key != clean_sku:
            candidates.append(clean_key)

        if not clean_key.startswith("XG"):
            candidates.extend([
                f"XG-{clean_sku}",
                f"XG - {clean_sku}",
                f"XG{clean_key}",
                re.sub(r"-", " - ", f"XG-{clean_sku}"),
            ])
        else:
            no_xg = re.sub(r"^XG\s*-?\s*", "", clean_sku)
            candidates.extend([
                no_xg,
                clean_sku_key(no_xg),
            ])

        # Cross-category alias for Electronics: EL <-> T
        if "EL" in clean_sku:
            t_sku = clean_sku.replace("EL", "T")
            candidates.extend([t_sku, clean_sku_key(t_sku)])
        elif "T" in clean_sku:
            el_sku = clean_sku.replace("T", "EL")
            candidates.extend([el_sku, clean_sku_key(el_sku)])

        seen = set()
        unique_candidates = [c for c in candidates if c and not (c in seen or seen.add(c))]

        var_str = ",".join(f'"{c}"' for c in unique_candidates)
        params = {
            "tenant_id": f"eq.{self.tenant_id}",
            "sku": f"in.({var_str})",
            "order": "quantity_from.asc",
        }
        res = self.client.select("pricing_rules", params)
        if not res:
            params = {
                "tenant_id": f"eq.{self.tenant_id}",
                "normalized_sku": f"in.({var_str})",
                "order": "quantity_from.asc",
            }
            res = self.client.select("pricing_rules", params)
        return res

    def calculate_price(self, sku: str, quantity: int) -> PriceQuoteResult:
        if quantity <= 0:
            return PriceQuoteResult(available=False, sku=sku, quantity=quantity, message="Quantity must be greater than 0")

        rules = self.get_rules_for_sku(sku)
        if not rules:
            return PriceQuoteResult(available=False, sku=sku, quantity=quantity, message=f"No pricing available for SKU {sku}")

        matched_rule = None
        for r in rules:
            q_from = int(r["quantity_from"])
            q_to = int(r["quantity_to"]) if r.get("quantity_to") is not None else None
            if q_from <= quantity and (q_to is None or quantity <= q_to):
                matched_rule = r
                break

        if not matched_rule:
            matched_rule = rules[-1]

        unit_base = float(matched_rule["unit_price_excl_gst"])
        gst_pct = float(matched_rule.get("gst_percentage", 18.0))
        unit_gst = round((unit_base * gst_pct) / 100.0, 2)
        unit_incl = round(unit_base + unit_gst, 2)
        total_base = round(unit_base * quantity, 2)
        total_gst = round(unit_gst * quantity, 2)
        total_incl = round(total_base + total_gst, 2)

        return PriceQuoteResult(
            available=True,
            sku=sku,
            quantity=quantity,
            unit_price_excl_gst=unit_base,
            gst_percentage=gst_pct,
            unit_gst=unit_gst,
            unit_price_incl_gst=unit_incl,
            total_price_excl_gst=total_base,
            total_gst=total_gst,
            total_price_incl_gst=total_incl,
            source=matched_rule.get("source_file"),
            source_file=matched_rule.get("source_file"),
            source_sheet=matched_rule.get("source_sheet"),
            source_row=matched_rule.get("source_row"),
            pricing_version=matched_rule.get("pricing_version", "2025"),
            bracket_from=matched_rule.get("quantity_from"),
            bracket_to=matched_rule.get("quantity_to"),
        )


# =============================================================================
# 3. SupabaseCustomerRepository
# =============================================================================
class SupabaseCustomerRepository:
    def __init__(self, client: Optional[SupabaseClient] = None, tenant_id: str = "default"):
        self.client = client or SupabaseClient()
        self.tenant_id = tenant_id

    def get_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        clean_phone = phone.lstrip("+").strip()
        res = self.client.select("customers", {"tenant_id": f"eq.{self.tenant_id}", "phone": f"eq.{clean_phone}", "limit": "1"})
        return res[0] if res else None

    def get_or_create(self, phone: str, name: Optional[str] = None) -> Dict[str, Any]:
        clean_phone = phone.lstrip("+").strip()
        existing = self.get_by_phone(clean_phone)
        if existing:
            if name and not existing.get("name"):
                self.client.update("customers", {"name": name}, {"tenant_id": f"eq.{self.tenant_id}", "phone": f"eq.{clean_phone}"})
                existing["name"] = name
            return existing

        new_cust = {
            "tenant_id": self.tenant_id,
            "phone": clean_phone,
            "name": name,
            "created_at": datetime.utcnow().isoformat(),
        }
        created = self.client.insert("customers", new_cust, on_conflict="tenant_id,phone")
        return created[0] if created else new_cust

    def list_customers(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.client.select("customers", {"tenant_id": f"eq.{self.tenant_id}", "order": "created_at.desc", "limit": str(limit)})


# =============================================================================
# 4. SupabaseConversationRepository
# =============================================================================
class SupabaseConversationRepository:
    def __init__(self, client: Optional[SupabaseClient] = None, tenant_id: str = "default"):
        self.client = client or SupabaseClient()
        self.tenant_id = tenant_id

    def get_or_create(self, phone: str) -> Dict[str, Any]:
        clean_phone = phone.lstrip("+").strip()
        conv_id = f"CONV-{clean_phone}"
        res = self.client.select("conversations", {"tenant_id": f"eq.{self.tenant_id}", "conversation_id": f"eq.{conv_id}", "limit": "1"})
        if res:
            return res[0]

        new_conv = {
            "tenant_id": self.tenant_id,
            "conversation_id": conv_id,
            "customer_phone": clean_phone,
            "channel": "WHATSAPP",
            "status": "ACTIVE",
            "current_product_candidates": [],
            "created_at": datetime.utcnow().isoformat(),
        }
        created = self.client.insert("conversations", new_conv, on_conflict="tenant_id,conversation_id")
        return created[0] if created else new_conv

    def update_state(self, conversation_id: str, updates: Dict[str, Any]) -> bool:
        res = self.client.update("conversations", updates, {"tenant_id": f"eq.{self.tenant_id}", "conversation_id": f"eq.{conversation_id}"})
        return bool(res)

    def get_by_id(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        res = self.client.select("conversations", {"tenant_id": f"eq.{self.tenant_id}", "conversation_id": f"eq.{conversation_id}", "limit": "1"})
        return res[0] if res else None

    def get_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        clean_phone = phone.lstrip("+").strip()
        res = self.client.select("conversations", {"tenant_id": f"eq.{self.tenant_id}", "customer_phone": f"eq.{clean_phone}", "limit": "1"})
        return res[0] if res else None

    def list_conversations(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.client.select("conversations", {"tenant_id": f"eq.{self.tenant_id}", "order": "updated_at.desc", "limit": str(limit)})


# =============================================================================
# 5. SupabaseMessageRepository
# =============================================================================
class SupabaseMessageRepository:
    def __init__(self, client: Optional[SupabaseClient] = None, tenant_id: str = "default"):
        self.client = client or SupabaseClient()
        self.tenant_id = tenant_id

    def add_message(
        self,
        conversation_id: str,
        direction: str,
        message_text: str,
        channel_message_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        msg = {
            "tenant_id": self.tenant_id,
            "conversation_id": conversation_id,
            "direction": direction,
            "message_text": message_text,
            "channel_message_id": channel_message_id,
            "payload": payload or {},
            "timestamp": datetime.utcnow().isoformat(),
        }
        res = self.client.insert("messages", msg)
        return res[0] if res else msg

    def get_messages(self, conversation_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        params = {
            "tenant_id": f"eq.{self.tenant_id}",
            "conversation_id": f"eq.{conversation_id}",
            "order": "timestamp.asc",
            "limit": str(limit),
        }
        return self.client.select("messages", params)

    def get_by_channel_message_id(self, channel_message_id: str):
        if not channel_message_id:
            return None
        params = {
            "tenant_id": f"eq.{self.tenant_id}",
            "channel_message_id": f"eq.{channel_message_id}",
            "limit": "1",
        }
        res = self.client.select("messages", params)
        return res[0] if res else None


# =============================================================================
# 6. SupabaseEnquiryRepository
# =============================================================================
class SupabaseEnquiryRepository:
    def __init__(self, client: Optional[SupabaseClient] = None, tenant_id: str = "default"):
        self.client = client or SupabaseClient()
        self.tenant_id = tenant_id

    def create_enquiry(self, enquiry_data: Dict[str, Any]) -> Dict[str, Any]:
        if "tenant_id" not in enquiry_data:
            enquiry_data["tenant_id"] = self.tenant_id
        if "enquiry_number" not in enquiry_data:
            now_str = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            enquiry_data["enquiry_number"] = f"ENQ-{now_str}"
        res = self.client.insert("enquiries", enquiry_data, on_conflict="tenant_id,enquiry_number")
        return res[0] if res else enquiry_data

    def get_by_number(self, enquiry_number: str) -> Optional[Dict[str, Any]]:
        res = self.client.select("enquiries", {"tenant_id": f"eq.{self.tenant_id}", "enquiry_number": f"eq.{enquiry_number}", "limit": "1"})
        return res[0] if res else None

    def update_status(self, enquiry_number: str, status: str) -> bool:
        res = self.client.update("enquiries", {"status": status}, {"tenant_id": f"eq.{self.tenant_id}", "enquiry_number": f"eq.{enquiry_number}"})
        return bool(res)

    def list_enquiries(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        params = {"tenant_id": f"eq.{self.tenant_id}", "order": "created_at.desc", "limit": str(limit)}
        if status:
            params["status"] = f"eq.{status}"
        return self.client.select("enquiries", params)


# =============================================================================
# 7. SupabaseQuoteRepository
# =============================================================================
class SupabaseQuoteRepository:
    def __init__(self, client: Optional[SupabaseClient] = None, tenant_id: str = "default"):
        self.client = client or SupabaseClient()
        self.tenant_id = tenant_id

    def create_quote(self, quote_data: Dict[str, Any]) -> Dict[str, Any]:
        if "tenant_id" not in quote_data:
            quote_data["tenant_id"] = self.tenant_id
        if "quote_number" not in quote_data:
            now_str = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            quote_data["quote_number"] = f"QUO-{now_str}"
        res = self.client.insert("quotes", quote_data, on_conflict="tenant_id,quote_number")
        return res[0] if res else quote_data

    def get_by_number(self, quote_number: str) -> Optional[Dict[str, Any]]:
        res = self.client.select("quotes", {"tenant_id": f"eq.{self.tenant_id}", "quote_number": f"eq.{quote_number}", "limit": "1"})
        return res[0] if res else None

    def update_status(self, quote_number: str, status: str) -> bool:
        res = self.client.update("quotes", {"status": status}, {"tenant_id": f"eq.{self.tenant_id}", "quote_number": f"eq.{quote_number}"})
        return bool(res)

    def list_quotes(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.client.select("quotes", {"tenant_id": f"eq.{self.tenant_id}", "order": "created_at.desc", "limit": str(limit)})


# =============================================================================
# 8. SupabaseOrderRepository
# =============================================================================
class SupabaseOrderRepository:
    def __init__(self, client: Optional[SupabaseClient] = None, tenant_id: str = "default"):
        self.client = client or SupabaseClient()
        self.tenant_id = tenant_id

    def create_order(self, order_data: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
        if "tenant_id" not in order_data:
            order_data["tenant_id"] = self.tenant_id
        order_id = order_data["order_id"]

        # Insert order
        res_order = self.client.insert("orders", order_data, on_conflict="tenant_id,order_id")

        # Insert items
        prepared_items = []
        for it in items:
            it_dict = dict(it)
            it_dict["tenant_id"] = self.tenant_id
            it_dict["order_id"] = order_id
            prepared_items.append(it_dict)

        if prepared_items:
            self.client.insert("order_items", prepared_items)

        # Record status history
        self.client.insert("order_status_history", {
            "tenant_id": self.tenant_id,
            "order_id": order_id,
            "new_status": order_data.get("status", "CONFIRMED"),
            "changed_by": "customer_whatsapp",
            "reason": "Order confirmed via WhatsApp flow",
            "created_at": datetime.utcnow().isoformat(),
        })

        out = dict(res_order[0] if res_order else order_data)
        out["items"] = prepared_items
        return out

    def get_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        res = self.client.select("orders", {"tenant_id": f"eq.{self.tenant_id}", "order_id": f"eq.{order_id}", "limit": "1"})
        if not res:
            return None
        order = dict(res[0])
        items = self.client.select("order_items", {"tenant_id": f"eq.{self.tenant_id}", "order_id": f"eq.{order_id}"})
        order["items"] = items
        return order

    def list_orders(self, limit: int = 100) -> List[Dict[str, Any]]:
        orders = self.client.select("orders", {"tenant_id": f"eq.{self.tenant_id}", "order": "created_at.desc", "limit": str(limit)})
        for ord_rec in orders:
            order_id = ord_rec.get("order_id")
            if order_id:
                items = self.client.select("order_items", {"tenant_id": f"eq.{self.tenant_id}", "order_id": f"eq.{order_id}"})
                ord_rec["items"] = items
        return orders

    def update_status(self, order_id: str, new_status: str, changed_by: str = "system", reason: Optional[str] = None) -> bool:
        current = self.get_by_id(order_id)
        prev_status = current.get("status") if current else None

        res = self.client.update("orders", {"status": new_status}, {"tenant_id": f"eq.{self.tenant_id}", "order_id": f"eq.{order_id}"})
        if res:
            self.client.insert("order_status_history", {
                "tenant_id": self.tenant_id,
                "order_id": order_id,
                "previous_status": prev_status,
                "new_status": new_status,
                "changed_by": changed_by,
                "reason": reason,
                "created_at": datetime.utcnow().isoformat(),
            })
            return True
        return False

    def get_order_status_history(self, order_id: str) -> List[Dict[str, Any]]:
        return self.client.select("order_status_history", {
            "tenant_id": f"eq.{self.tenant_id}",
            "order_id": f"eq.{order_id}",
            "order": "created_at.asc",
        })


# =============================================================================
# 9. SupabaseAuditRepository
# =============================================================================
class SupabaseAuditRepository:
    """
    Durable Supabase repository for WhatsApp message observability, delivery
    status tracking, and 24h dashboard reliability metrics.
    """

    def __init__(self, client: Optional[SupabaseClient] = None, tenant_id: str = "default"):
        self.client = client or SupabaseClient()
        self.tenant_id = tenant_id

    @property
    def is_configured(self) -> bool:
        return self.client.is_configured

    def record_event(
        self,
        correlation_id: str,
        customer_phone: str = "UNKNOWN",
        direction: str = "INBOUND",
        event_type: str = "UNKNOWN",
        status: Optional[str] = None,
        wamid: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        is_test: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Persists a lifecycle audit event to the durable Supabase store."""
        if not self.is_configured:
            return None

        clean_phone = customer_phone.lstrip("+").strip() if customer_phone else "UNKNOWN"
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "tenant_id": self.tenant_id,
            "correlation_id": correlation_id,
            "channel_message_id": wamid,
            "customer_phone": clean_phone or "UNKNOWN",
            "direction": direction.upper(),
            "event_type": event_type.upper(),
            "status": status.upper() if status else None,
            "details": details or {},
            "is_test": bool(is_test),
            "created_at": now_iso,
        }
        try:
            res = self.client.insert("message_audit_events", payload)
            return res[0] if res else None
        except Exception as exc:
            logger.error("Supabase audit insert failed for %s (%s): %s", correlation_id, event_type, exc)
            raise

    def get_metrics_summary(self, hours: int = 24, production_only: bool = True) -> Dict[str, Any]:
        """
        Calculates operational health and volume metrics from the durable Supabase store
        over a specified rolling window (default 24h).
        Accurately computes failure rate percentage when denominator > 0.
        """
        if not self.is_configured:
            return {
                "total_received": 0,
                "total_inbound": 0,
                "total_sent": 0,
                "total_accepted": 0,
                "total_delivered": 0,
                "total_read": 0,
                "total_failed": 0,
                "failure_rate_percent": None,
                "hours_window": hours,
                "last_inbound": None,
                "last_outbound": None,
                "last_failure": None,
            }

        cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        params = {
            "tenant_id": f"eq.{self.tenant_id}",
            "created_at": f"gte.{cutoff_iso}",
            "order": "created_at.desc",
            "limit": "5000",
        }
        if production_only:
            params["is_test"] = "eq.false"

        try:
            events = self.client.select("message_audit_events", params)
        except Exception as exc:
            logger.error("Supabase select message_audit_events failed: %s", exc)
            events = []

        total_received = sum(1 for e in events if e.get("event_type") == "RECEIVED")
        total_accepted = sum(1 for e in events if e.get("event_type") == "META_ACCEPTED")
        total_sent = total_accepted + sum(1 for e in events if e.get("event_type") == "SENT")
        total_delivered = sum(1 for e in events if e.get("event_type") == "DELIVERED")
        total_read = sum(1 for e in events if e.get("event_type") == "READ")
        total_failed = sum(1 for e in events if e.get("event_type") == "FAILED" or e.get("status") == "FAILED")

        outbound_attempts = total_sent + total_failed
        if outbound_attempts > 0:
            failure_rate_percent = round((total_failed / outbound_attempts) * 100.0, 1)
        else:
            failure_rate_percent = None

        # Last inbound in 24h window (or lifetime query)
        last_inbound = next(
            (
                e for e in events
                if e.get("direction") == "INBOUND"
                and e.get("event_type") == "RECEIVED"
                and e.get("customer_phone")
                and e.get("customer_phone") != "UNKNOWN"
            ),
            None,
        )
        if not last_inbound:
            last_in_params = {
                "tenant_id": f"eq.{self.tenant_id}",
                "direction": "eq.INBOUND",
                "event_type": "eq.RECEIVED",
                "order": "created_at.desc",
                "limit": "1",
            }
            if production_only:
                last_in_params["is_test"] = "eq.false"
                last_in_params["customer_phone"] = "neq.UNKNOWN"
            res = self.client.select("message_audit_events", last_in_params)
            last_inbound = res[0] if res else None

        # Last outbound in 24h window (or lifetime query)
        last_outbound = next(
            (
                e for e in events
                if e.get("direction") == "OUTBOUND"
                and e.get("event_type") in ("META_ACCEPTED", "SENT")
                and e.get("customer_phone")
                and e.get("customer_phone") != "UNKNOWN"
            ),
            None,
        )
        if not last_outbound:
            last_out_params = {
                "tenant_id": f"eq.{self.tenant_id}",
                "direction": "eq.OUTBOUND",
                "event_type": "in.(META_ACCEPTED,SENT)",
                "order": "created_at.desc",
                "limit": "1",
            }
            if production_only:
                last_out_params["is_test"] = "eq.false"
                last_out_params["customer_phone"] = "neq.UNKNOWN"
            res = self.client.select("message_audit_events", last_out_params)
            last_outbound = res[0] if res else None

        # Last failure
        last_failure = next(
            (e for e in events if e.get("event_type") == "FAILED" or e.get("status") == "FAILED"),
            None,
        )
        if not last_failure:
            last_fail_params = {
                "tenant_id": f"eq.{self.tenant_id}",
                "or": "(event_type.eq.FAILED,status.eq.FAILED)",
                "order": "created_at.desc",
                "limit": "1",
            }
            if production_only:
                last_fail_params["is_test"] = "eq.false"
            res = self.client.select("message_audit_events", last_fail_params)
            last_failure = res[0] if res else None

        return {
            "total_received": total_received,
            "total_inbound": total_received,
            "total_sent": total_sent,
            "total_accepted": total_accepted,
            "total_delivered": total_delivered,
            "total_read": total_read,
            "total_failed": total_failed,
            "failure_rate_percent": failure_rate_percent,
            "hours_window": hours,
            "last_inbound": last_inbound,
            "last_outbound": last_outbound,
            "last_failure": last_failure,
        }

    def get_last_event_by_direction(self, direction: str, production_only: bool = True) -> Optional[Dict[str, Any]]:
        """Returns the most recent event for a given direction."""
        if not self.is_configured:
            return None
        params = {
            "tenant_id": f"eq.{self.tenant_id}",
            "direction": f"eq.{direction.upper()}",
            "order": "created_at.desc",
            "limit": "1",
        }
        if production_only:
            params["is_test"] = "eq.false"
            params["customer_phone"] = "neq.UNKNOWN"
        res = self.client.select("message_audit_events", params)
        return res[0] if res else None

    def get_recent_failures(self, limit: int = 5, production_only: bool = True) -> List[Dict[str, Any]]:
        """Returns recent failure events."""
        if not self.is_configured:
            return []
        params = {
            "tenant_id": f"eq.{self.tenant_id}",
            "or": "(event_type.eq.FAILED,status.eq.FAILED)",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        if production_only:
            params["is_test"] = "eq.false"
        rows = self.client.select("message_audit_events", params)
        failures = []
        for r in rows:
            det = r.get("details", {})
            failures.append({
                "correlation_id": r.get("correlation_id"),
                "phone": r.get("customer_phone"),
                "timestamp": r.get("created_at"),
                "code": det.get("error_code") if isinstance(det, dict) else None,
                "type": det.get("error_type") if isinstance(det, dict) else None,
                "error_message": det.get("error_message") if isinstance(det, dict) else None,
            })
        return failures

    def get_recent_events(
        self,
        limit: int = 50,
        correlation_id: Optional[str] = None,
        wamid: Optional[str] = None,
        phone: Optional[str] = None,
        event_type: Optional[str] = None,
        production_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Returns recent audit events, optionally filtered."""
        if not self.is_configured:
            return []
        params = {
            "tenant_id": f"eq.{self.tenant_id}",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        if production_only and not correlation_id and not wamid:
            params["is_test"] = "eq.false"
        if correlation_id:
            params["correlation_id"] = f"eq.{correlation_id}"
        if wamid:
            params["channel_message_id"] = f"eq.{wamid}"
        if phone:
            params["customer_phone"] = f"eq.{phone.lstrip('+').strip()}"
        if event_type:
            params["event_type"] = f"eq.{event_type.upper()}"

        return self.client.select("message_audit_events", params)

    def lookup_phone_by_wamid(self, wamid: str) -> Optional[str]:
        """Looks up the customer phone associated with an outbound wamid."""
        if not self.is_configured or not wamid:
            return None
        params = {
            "tenant_id": f"eq.{self.tenant_id}",
            "channel_message_id": f"eq.{wamid}",
            "customer_phone": "neq.UNKNOWN",
            "limit": "1",
        }
        res = self.client.select("message_audit_events", params)
        if res and res[0].get("customer_phone"):
            return res[0]["customer_phone"]
        return None


# =============================================================================
# 11. SupabaseInventoryRepository
# =============================================================================
class SupabaseInventoryRepository:
    """
    Durable Supabase PostgreSQL repository for inventory stock levels
    and immutable inventory_transactions audit history.
    Multi-tenant aware: Logical uniqueness on (tenant_id, sku).
    All reads and writes are strictly scoped to self.tenant_id.
    """

    def __init__(
        self,
        client: Optional[SupabaseClient] = None,
        tenant_id: str = "default",
    ):
        self.client = client or SupabaseClient()
        self.tenant_id = tenant_id

    @property
    def is_configured(self) -> bool:
        return self.client.is_configured

    def get_inventory(self, sku: str) -> Optional[Dict[str, Any]]:
        """Fetches a single inventory record scoped to tenant_id and sku."""
        if not self.is_configured:
            return None
        norm_sku = sku.strip().upper()
        params = {
            "tenant_id": f"eq.{self.tenant_id}",
            "sku": f"eq.{norm_sku}",
            "limit": "1",
        }
        res = self.client.select("inventory", params)
        return res[0] if res else None

    def get_all_inventory_map(self, tenant_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Fetches all inventory records for the tenant as a dictionary keyed by SKU."""
        if not self.is_configured:
            return {}
        target_tenant = tenant_id or self.tenant_id
        params = {"tenant_id": f"eq.{target_tenant}"}
        res = self.client.select("inventory", params)
        return {row["sku"].strip().upper(): row for row in res if "sku" in row}

    def upsert_inventory_item(
        self,
        sku: str,
        physical_quantity: Optional[int] = None,
        reserved_quantity: int = 0,
        reorder_level: int = 100,
        unit_cost: Optional[float] = None,
        status: str = "UNKNOWN",
        supplier_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Inserts or updates an inventory record with composite conflict resolution on (tenant_id, sku).
        Preserves UNKNOWN state when physical_quantity is None.
        """
        if not self.is_configured:
            raise RuntimeError("Supabase production inventory repository is not configured")
        norm_sku = sku.strip().upper()
        now_iso = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "sku": norm_sku,
            "physical_quantity": physical_quantity,
            "reserved_quantity": reserved_quantity,
            "reorder_level": reorder_level,
            "status": status,
            "last_updated": now_iso,
        }
        if unit_cost is not None:
            payload["unit_cost"] = unit_cost
        if supplier_id is not None:
            payload["supplier_id"] = supplier_id

        res = self.client.insert("inventory", payload, on_conflict="tenant_id,sku")
        if not res:
            raise RuntimeError(f"Failed to upsert inventory record for {norm_sku} in Supabase")
        return res[0] if isinstance(res, list) and res else payload

    def record_transaction(
        self,
        sku: str,
        transaction_type: str,
        quantity_change: int,
        quantity_before: Optional[int],
        quantity_after: Optional[int],
        reason: Optional[str] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        notes: Optional[str] = None,
        created_by: str = "owner",
    ) -> Optional[Dict[str, Any]]:
        """Appends an immutable audit record to inventory_transactions in Supabase."""
        if not self.is_configured:
            raise RuntimeError("Supabase production inventory repository is not configured")
        norm_sku = sku.strip().upper()
        now_iso = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "sku": norm_sku,
            "transaction_type": transaction_type,
            "quantity_change": quantity_change,
            "quantity_before": quantity_before,
            "quantity_after": quantity_after,
            "reason": reason or "",
            "reference_type": reference_type,
            "reference_id": reference_id,
            "notes": notes,
            "created_by": created_by,
            "created_at": now_iso,
        }
        res = self.client.insert("inventory_transactions", payload)
        if not res:
            raise RuntimeError(f"Failed to persist inventory transaction for {norm_sku} in Supabase")
        return res[0] if isinstance(res, list) and res else payload

    def get_transactions(
        self,
        sku: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Fetches recent transactions for the tenant, optionally filtered by SKU."""
        if not self.is_configured:
            return []
        params: Dict[str, Any] = {
            "tenant_id": f"eq.{self.tenant_id}",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        if sku:
            params["sku"] = f"eq.{sku.strip().upper()}"
        return self.client.select("inventory_transactions", params)

    def delete_inventory(self, sku: str) -> bool:
        """Deletes an inventory item scoped to tenant_id and sku (useful for tests)."""
        if not self.is_configured:
            return False
        norm_sku = sku.strip().upper()
        return self.client.delete(
            "inventory",
            {"tenant_id": f"eq.{self.tenant_id}", "sku": f"eq.{norm_sku}"},
        )

    def delete_transaction(self, tx_id: str) -> bool:
        """Deletes a transaction record by ID (useful for tests)."""
        if not self.is_configured:
            return False
        return self.client.delete(
            "inventory_transactions",
            {"tenant_id": f"eq.{self.tenant_id}", "id": f"eq.{tx_id}"},
        )
