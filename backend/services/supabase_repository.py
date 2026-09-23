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
from datetime import datetime
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
