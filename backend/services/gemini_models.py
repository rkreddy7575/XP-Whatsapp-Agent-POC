from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class IntentType(str, Enum):
    PRODUCT_SEARCH = "PRODUCT_SEARCH"
    PRODUCT_DETAILS = "PRODUCT_DETAILS"
    PRICE_QUOTE = "PRICE_QUOTE"
    SELECT_PRODUCT = "SELECT_PRODUCT"
    CONFIRM_ORDER = "CONFIRM_ORDER"
    CANCEL_ORDER = "CANCEL_ORDER"
    ORDER_STATUS = "ORDER_STATUS"
    GENERAL_HELP = "GENERAL_HELP"
    UNKNOWN = "UNKNOWN"


class StructuredIntent(BaseModel):
    """
    Structured intent output produced by Gemini conversational layer.
    Strictly restricted to intent and parameter extraction.
    Never calculates prices, totals, GST, or modifies database directly.
    """
    intent: IntentType = Field(default=IntentType.UNKNOWN, description="Identified customer intent")
    category: Optional[str] = Field(default=None, description="Category name if mentioned (e.g. 'Gift Sets', 'Writing Instruments')")
    query: Optional[str] = Field(default=None, description="Search keywords or item description")
    sku: Optional[str] = Field(default=None, description="Explicit product SKU if mentioned (e.g. 'XG-GS-501')")
    quantity: Optional[int] = Field(default=None, description="Product quantity requested (e.g. 100)")
    budget_per_unit: Optional[float] = Field(default=None, description="Maximum budget or target price per unit (e.g. 500.0)")
    selection_index: Optional[int] = Field(default=None, description="1-based candidate item index (e.g. 1 for 'first', 2 for 'second')")
    order_id: Optional[str] = Field(default=None, description="Order ID if checking status (e.g. 'ORD-20260919-0001')")
    notes: Optional[str] = Field(default=None, description="Optional reasoning or context notes")
