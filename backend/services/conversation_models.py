from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MessageDirection(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


@dataclass
class ConversationMessage:
    """Represents a single message in the conversation history."""
    id: Optional[int]
    conversation_id: str
    direction: MessageDirection
    message_text: str
    timestamp: str


@dataclass
class Conversation:
    """Represents the multi-turn conversational state for a customer."""
    conversation_id: str
    customer_phone: str
    last_intent: Optional[str] = None
    current_product_candidates: List[Dict[str, Any]] = field(default_factory=list)
    selected_sku: Optional[str] = None
    selected_quantity: Optional[int] = None
    pending_quote: Optional[Dict[str, Any]] = None
    created_at: str = ""
    updated_at: str = ""
