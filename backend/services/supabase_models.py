"""
Supabase Data Models & Schemas
Compatible with Supabase PostgreSQL schema (Migration 001).
Maintains strict multi-tenant structure (tenant_id) while preserving
authoritative data from catalogue_master.json and pricing_master.json.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import re


def normalize_sku(sku: str) -> str:
    """
    Standardizes casing and collapses whitespace without stripping hyphens.
    E.g. '  xg-bt-001  ' -> 'XG-BT-001'
    """
    if not sku:
        return ""
    return re.sub(r"\s+", " ", str(sku)).strip().upper()


def clean_sku_key(sku: str) -> str:
    """
    Strips all punctuation and spaces for alphanumeric index lookup.
    E.g. 'XG-BT-001' -> 'XGBT001'
    """
    if not sku:
        return ""
    return re.sub(r"[^A-Z0-9]", "", sku.upper())


@dataclass
class ProductRecord:
    sku: str
    category: str
    tenant_id: str = "default"
    normalized_sku: str = ""
    name: Optional[str] = None
    subcategory: Optional[str] = None
    description: Optional[str] = None
    components: List[str] = field(default_factory=list)
    colors: List[str] = field(default_factory=list)
    moq: Optional[int] = None
    image_url: Optional[str] = None
    status: str = "ACTIVE"
    source_file: Optional[str] = None
    source_sheet: Optional[str] = None
    source_row: Optional[int] = None
    source_price: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.sku = normalize_sku(self.sku)
        if not self.normalized_sku:
            self.normalized_sku = self.sku

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PricingRuleRecord:
    sku: str
    unit_price_excl_gst: float
    tenant_id: str = "default"
    normalized_sku: str = ""
    category: Optional[str] = None
    quantity_from: int = 1
    quantity_to: Optional[int] = None
    gst_percentage: float = 18.0
    source_file: Optional[str] = None
    source_sheet: Optional[str] = None
    source_row: Optional[int] = None
    source_category: Optional[str] = None
    pricing_version: str = "2025"
    status: str = "ACTIVE"

    def __post_init__(self):
        self.sku = normalize_sku(self.sku)
        if not self.normalized_sku:
            self.normalized_sku = self.sku
        self.unit_price_excl_gst = round(float(self.unit_price_excl_gst), 2)
        self.gst_percentage = round(float(self.gst_percentage), 2)
        self.quantity_from = int(self.quantity_from)
        if self.quantity_to is not None:
            self.quantity_to = int(self.quantity_to)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CustomerRecord:
    phone: str
    tenant_id: str = "default"
    name: Optional[str] = None
    email: Optional[str] = None
    company_name: Optional[str] = None
    gstin: Optional[str] = None
    billing_address: Optional[str] = None
    shipping_address: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationRecord:
    conversation_id: str
    customer_phone: str
    tenant_id: str = "default"
    channel: str = "WHATSAPP"
    last_intent: Optional[str] = None
    current_product_candidates: List[Dict[str, Any]] = field(default_factory=list)
    selected_sku: Optional[str] = None
    selected_quantity: Optional[int] = None
    pending_quote: Optional[Dict[str, Any]] = None
    status: str = "ACTIVE"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MessageRecord:
    conversation_id: str
    direction: str  # INBOUND or OUTBOUND
    message_text: str
    tenant_id: str = "default"
    channel_message_id: Optional[str] = None
    sender_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EnquiryRecord:
    enquiry_number: str
    customer_phone: str
    tenant_id: str = "default"
    customer_name: Optional[str] = None
    conversation_id: Optional[str] = None
    status: str = "NEW"
    category: Optional[str] = None
    sku: Optional[str] = None
    quantity: Optional[int] = None
    customization_details: Optional[str] = None
    budget_per_unit: Optional[float] = None
    target_date: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QuoteRecord:
    quote_number: str
    customer_phone: str
    sku: str
    quantity: int
    unit_price_excl_gst: float
    gst_percentage: float
    unit_gst: float
    unit_price_incl_gst: float
    total_price_excl_gst: float
    total_gst: float
    total_price_incl_gst: float
    tenant_id: str = "default"
    pricing_source: Optional[str] = None
    pricing_version: str = "2025"
    status: str = "ISSUED"
    quote_data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OrderRecord:
    order_id: str
    customer_phone: str
    subtotal: float
    gst_amount: float
    grand_total: float
    tenant_id: str = "default"
    customer_name: Optional[str] = None
    status: str = "CONFIRMED"
    currency: str = "INR"
    pricing_version: str = "2025"
    inventory_status: str = "availability_confirmation_required"
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OrderItemRecord:
    order_id: str
    sku: str
    quantity: int
    unit_price: float
    gst_rate: float
    gst_amount: float
    line_total: float
    tenant_id: str = "default"
    product_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OrderStatusHistoryRecord:
    order_id: str
    new_status: str
    tenant_id: str = "default"
    previous_status: Optional[str] = None
    changed_by: str = "system"
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InventoryRecord:
    sku: str
    tenant_id: str = "default"
    normalized_sku: str = ""
    physical_quantity: Optional[int] = None
    reserved_quantity: int = 0
    reorder_level: int = 100
    unit_cost: Optional[float] = None
    status: str = "UNKNOWN"
    supplier_id: Optional[str] = None
    last_updated: Optional[str] = None
    created_at: Optional[str] = None
    id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.sku = normalize_sku(self.sku)
        if not self.normalized_sku:
            self.normalized_sku = self.sku
        if self.physical_quantity is not None:
            self.physical_quantity = max(0, int(self.physical_quantity))
        self.reserved_quantity = max(0, int(self.reserved_quantity or 0))
        self.reorder_level = max(0, int(self.reorder_level or 0))
        if self.unit_cost is not None:
            self.unit_cost = round(float(self.unit_cost), 2)

    @property
    def available_quantity(self) -> Optional[int]:
        if self.physical_quantity is None:
            return None
        return max(0, self.physical_quantity - self.reserved_quantity)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["available_quantity"] = self.available_quantity
        if not d.get("id"):
            d.pop("id", None)
        return d


@dataclass
class InventoryTransactionRecord:
    sku: str
    transaction_type: str
    quantity_change: int
    quantity_before: int
    quantity_after: int
    id: Optional[str] = None
    tenant_id: str = "default"
    reason: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    notes: Optional[str] = None
    created_by: str = "owner"
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MessageAuditEventRecord:
    correlation_id: str
    direction: str  # INBOUND or OUTBOUND
    event_type: str  # RECEIVED, META_ACCEPTED, SENT, DELIVERED, READ, FAILED
    tenant_id: str = "default"
    channel_message_id: Optional[str] = None  # wamid
    customer_phone: str = "UNKNOWN"
    status: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    is_test: bool = False
    created_at: Optional[str] = None
    id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not d.get("id"):
            d.pop("id", None)
        return d
