from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class OrderStatus(str, Enum):
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    PROCESSING = "PROCESSING"
    READY_FOR_DISPATCH = "READY_FOR_DISPATCH"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


@dataclass
class OrderItem:
    """Represents an immutable price-snapshotted item within an order."""
    sku: str
    quantity: int
    unit_price: float         # Excl. GST unit price snapshot
    gst_rate: float           # GST percentage snapshot (e.g. 18.0)
    gst_amount: float         # Total GST for this line snapshot
    line_total: float         # Grand total including GST for this line


@dataclass
class Order:
    """Represents a customer order."""
    order_id: str
    customer_phone: str
    status: OrderStatus
    items: List[OrderItem]
    subtotal: float           # Excl. GST
    gst_amount: float         # Total GST
    grand_total: float        # Incl. GST
    customer_name: Optional[str] = None
    currency: str = "INR"
    created_at: str = ""
    updated_at: str = ""
    pricing_version: str = "2025"
    inventory_status: str = "availability_confirmation_required"
