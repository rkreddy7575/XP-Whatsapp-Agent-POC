import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from services.conversation_models import (
    Conversation,
    ConversationMessage,
    MessageDirection,
)

DEFAULT_PROD_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "app.db")
)
DEFAULT_TEST_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "test_app.db")
)


def get_default_db_path() -> str:
    """
    Returns the appropriate database path based on configuration and test environment.
    Strictly isolates automated tests from the production database.
    """
    if os.getenv("DATABASE_PATH"):
        return os.path.abspath(os.getenv("DATABASE_PATH"))
    if os.getenv("TESTING") in ("1", "true", "True") or any(
        "unittest" in str(arg).lower() or "pytest" in str(arg).lower()
        for arg in sys.argv
    ):
        return DEFAULT_TEST_DB_PATH
    return DEFAULT_PROD_DB_PATH


# Preserved for backward compatibility
DEFAULT_DB_PATH = DEFAULT_PROD_DB_PATH


class ConversationService:
    """
    SQLite-backed conversation state service for WhatsApp customer interactions.
    Tracks multi-turn context (selected product, quantity, candidates, pending quotes, message history).
    """

    def __init__(self, db_path: Optional[str] = None):
        self._explicit_db_path = db_path
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._initialized_paths: Set[str] = set()

        if self._explicit_db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            self._init_db(self._mem_conn)
        else:
            self._ensure_initialized()

    @property
    def db_path(self) -> str:
        if self._explicit_db_path is not None:
            return self._explicit_db_path
        return get_default_db_path()

    @db_path.setter
    def db_path(self, value: Optional[str]):
        self._explicit_db_path = value

    def _ensure_initialized(self, target_path: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> None:
        target = target_path or self.db_path
        if target in self._initialized_paths:
            return
        if conn is not None:
            self._init_db(conn)
            self._initialized_paths.add(target)
        else:
            if target != ":memory:":
                os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
                with sqlite3.connect(target, check_same_thread=False) as c:
                    c.row_factory = sqlite3.Row
                    c.execute("PRAGMA journal_mode=WAL;")
                    c.execute("PRAGMA busy_timeout=5000;")
                    self._init_db(c)
                self._initialized_paths.add(target)

    def _get_connection(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        target = self.db_path
        if target == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem_conn.row_factory = sqlite3.Row
                self._init_db(self._mem_conn)
            return self._mem_conn
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        conn = sqlite3.connect(target, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL mode and busy timeout for high concurrency and resilience
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        self._ensure_initialized(target, conn)
        return conn

    def _init_db(self, conn: Optional[sqlite3.Connection] = None) -> None:
        """Initializes SQLite schema for conversations and message history."""
        if conn is not None:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    customer_phone TEXT UNIQUE NOT NULL,
                    last_intent TEXT,
                    current_product_candidates TEXT,
                    selected_sku TEXT,
                    selected_quantity INTEGER,
                    pending_quote TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations (conversation_id)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversations_phone ON conversations (customer_phone);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_conv_id ON conversation_messages (conversation_id);
            """)
            conn.commit()
        else:
            with self._get_connection() as c:
                self._init_db(c)


    @staticmethod
    def _normalize_phone(phone: Optional[str]) -> str:
        if not phone:
            return ""
        return re.sub(r"[^0-9]", "", str(phone))

    def _row_to_conversation(self, row: sqlite3.Row) -> Conversation:
        candidates_raw = row["current_product_candidates"]
        candidates = json.loads(candidates_raw) if candidates_raw else []

        pending_quote_raw = row["pending_quote"]
        pending_quote = json.loads(pending_quote_raw) if pending_quote_raw else None

        return Conversation(
            conversation_id=row["conversation_id"],
            customer_phone=row["customer_phone"],
            last_intent=row["last_intent"],
            current_product_candidates=candidates,
            selected_sku=row["selected_sku"],
            selected_quantity=row["selected_quantity"],
            pending_quote=pending_quote,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_or_create_conversation(self, customer_phone: str) -> Conversation:
        """Retrieves existing active conversation or creates a new one for customer_phone."""
        clean_phone = self._normalize_phone(customer_phone)
        if not clean_phone:
            raise ValueError("Valid customer phone number is required.")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM conversations WHERE customer_phone = ?", (clean_phone,))
            row = cursor.fetchone()
            if row:
                return self._row_to_conversation(row)

            now_iso = datetime.now().isoformat()
            conv_id = f"CONV-{clean_phone}"
            cursor.execute(
                """
                INSERT INTO conversations (
                    conversation_id, customer_phone, last_intent, current_product_candidates,
                    selected_sku, selected_quantity, pending_quote, created_at, updated_at
                ) VALUES (?, ?, NULL, '[]', NULL, NULL, NULL, ?, ?)
                """,
                (conv_id, clean_phone, now_iso, now_iso),
            )
            conn.commit()

            cursor.execute("SELECT * FROM conversations WHERE conversation_id = ?", (conv_id,))
            return self._row_to_conversation(cursor.fetchone())

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Retrieves conversation by conversation_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,))
            row = cursor.fetchone()
            return self._row_to_conversation(row) if row else None

    def get_conversation_by_phone(self, customer_phone: str) -> Optional[Conversation]:
        """Retrieves conversation by customer_phone without creating one."""
        clean_phone = self._normalize_phone(customer_phone)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM conversations WHERE customer_phone = ?", (clean_phone,))
            row = cursor.fetchone()
            return self._row_to_conversation(row) if row else None

    def add_message(
        self,
        conversation_id: str,
        direction: MessageDirection,
        message_text: str,
    ) -> ConversationMessage:
        """Appends an incoming or outgoing message to message history."""
        now_iso = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO conversation_messages (conversation_id, direction, message_text, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, direction.value, message_text, now_iso),
            )
            msg_id = cursor.lastrowid
            cursor.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now_iso, conversation_id),
            )
            conn.commit()
            return ConversationMessage(
                id=msg_id,
                conversation_id=conversation_id,
                direction=direction,
                message_text=message_text,
                timestamp=now_iso,
            )

    def get_recent_messages(
        self, conversation_id: str, limit: int = 10
    ) -> List[ConversationMessage]:
        """Returns the most recent messages in chronological order."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM (
                    SELECT * FROM conversation_messages
                    WHERE conversation_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (conversation_id, limit),
            )
            rows = cursor.fetchall()
            return [
                ConversationMessage(
                    id=r["id"],
                    conversation_id=r["conversation_id"],
                    direction=MessageDirection(r["direction"]),
                    message_text=r["message_text"],
                    timestamp=r["timestamp"],
                )
                for r in rows
            ]

    def set_candidates(
        self, conversation_id: str, candidates: List[Dict[str, Any]]
    ) -> None:
        """Stores candidate product search results in conversation state."""
        now_iso = datetime.now().isoformat()
        serialized = json.dumps(candidates)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE conversations
                SET current_product_candidates = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (serialized, now_iso, conversation_id),
            )
            conn.commit()

    def get_candidate_by_index(
        self, conversation_id: str, index: int
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves candidate product by 1-based index (e.g. index 1 = first item, index 2 = second item).
        """
        conv = self.get_conversation(conversation_id)
        if not conv or not conv.current_product_candidates:
            return None
        # Convert 1-based index to 0-based
        zero_idx = index - 1
        if 0 <= zero_idx < len(conv.current_product_candidates):
            return conv.current_product_candidates[zero_idx]
        return None

    def set_selected_product(
        self,
        conversation_id: str,
        sku: str,
        quantity: Optional[int] = None,
    ) -> None:
        """Sets selected product SKU and optional quantity."""
        now_iso = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if quantity is not None:
                cursor.execute(
                    """
                    UPDATE conversations
                    SET selected_sku = ?, selected_quantity = ?, updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (sku, quantity, now_iso, conversation_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE conversations
                    SET selected_sku = ?, updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (sku, now_iso, conversation_id),
                )
            conn.commit()

    def set_selected_quantity(self, conversation_id: str, quantity: int) -> None:
        """Sets selected quantity."""
        now_iso = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE conversations
                SET selected_quantity = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (quantity, now_iso, conversation_id),
            )
            conn.commit()

    def set_pending_quote(self, conversation_id: str, quote: Any) -> None:
        """Caches active quote in conversation state (accepts PriceQuoteResult or dict)."""
        now_iso = datetime.now().isoformat()
        if hasattr(quote, "__dict__"):
            data = quote.__dict__
        elif isinstance(quote, dict):
            data = quote
        else:
            data = {"quote": str(quote)}

        serialized = json.dumps(data)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE conversations
                SET pending_quote = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (serialized, now_iso, conversation_id),
            )
            conn.commit()

    def get_pending_quote(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached quote for conversation."""
        conv = self.get_conversation(conversation_id)
        return conv.pending_quote if conv else None

    def clear_pending_quote(self, conversation_id: str) -> None:
        """Clears active quote from conversation state."""
        now_iso = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE conversations
                SET pending_quote = NULL, updated_at = ?
                WHERE conversation_id = ?
                """,
                (now_iso, conversation_id),
            )
            conn.commit()

    def clear_selection(self, conversation_id: str) -> None:
        """Clears current candidates, selection, and pending quote."""
        now_iso = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE conversations
                SET current_product_candidates = '[]',
                    selected_sku = NULL,
                    selected_quantity = NULL,
                    pending_quote = NULL,
                    updated_at = ?
                WHERE conversation_id = ?
                """,
                (now_iso, conversation_id),
            )
            conn.commit()

    def update_last_intent(self, conversation_id: str, intent: str) -> None:
        """Updates last identified intent."""
        now_iso = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE conversations SET last_intent = ?, updated_at = ? WHERE conversation_id = ?",
                (intent, now_iso, conversation_id),
            )
            conn.commit()

    def list_active_enquiries(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Retrieves recent customer WhatsApp conversations and enquiries for the Owner Dashboard,
        including customer details, last enquiry message, products discussed, quotation status, and order status.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'")
            has_orders_table = bool(cursor.fetchone())

            if has_orders_table:
                sql = """
                    SELECT 
                        c.conversation_id,
                        c.customer_phone,
                        c.last_intent,
                        c.current_product_candidates,
                        c.selected_sku,
                        c.selected_quantity,
                        c.pending_quote,
                        c.created_at,
                        c.updated_at,
                        (SELECT cm.message_text 
                         FROM conversation_messages cm 
                         WHERE cm.conversation_id = c.conversation_id AND UPPER(cm.direction) = 'INBOUND' 
                         ORDER BY cm.id DESC LIMIT 1) as last_inbound,
                        (SELECT o.customer_name 
                         FROM orders o 
                         WHERE o.customer_phone = c.customer_phone 
                         ORDER BY o.created_at DESC LIMIT 1) as customer_name,
                        (SELECT o.order_id 
                         FROM orders o 
                         WHERE o.customer_phone = c.customer_phone 
                         ORDER BY o.created_at DESC LIMIT 1) as latest_order_id,
                        (SELECT o.status 
                         FROM orders o 
                         WHERE o.customer_phone = c.customer_phone 
                         ORDER BY o.created_at DESC LIMIT 1) as latest_order_status
                    FROM conversations c
                    ORDER BY c.updated_at DESC
                    LIMIT ?
                """
            else:
                sql = """
                    SELECT 
                        c.conversation_id,
                        c.customer_phone,
                        c.last_intent,
                        c.current_product_candidates,
                        c.selected_sku,
                        c.selected_quantity,
                        c.pending_quote,
                        c.created_at,
                        c.updated_at,
                        (SELECT cm.message_text 
                         FROM conversation_messages cm 
                         WHERE cm.conversation_id = c.conversation_id AND UPPER(cm.direction) = 'INBOUND' 
                         ORDER BY cm.id DESC LIMIT 1) as last_inbound,
                        NULL as customer_name,
                        NULL as latest_order_id,
                        NULL as latest_order_status
                    FROM conversations c
                    ORDER BY c.updated_at DESC
                    LIMIT ?
                """

            cursor.execute(sql, (limit,))
            rows = cursor.fetchall()
            results = []
            for r in rows:
                pq_raw = r["pending_quote"]
                pq = json.loads(pq_raw) if pq_raw else None
                cand_raw = r["current_product_candidates"]
                cands = json.loads(cand_raw) if cand_raw else []

                # Compile products discussed
                products = []
                if r["selected_sku"]:
                    products.append(r["selected_sku"])
                for cand in cands:
                    csku = cand.get("sku") if isinstance(cand, dict) else str(cand)
                    if csku and csku not in products:
                        products.append(csku)

                # Determine quotation status
                if pq:
                    quote_status = "ACTIVE_QUOTE"
                elif r["latest_order_id"]:
                    quote_status = "ORDERED"
                elif r["selected_sku"]:
                    quote_status = "PRODUCT_SELECTED"
                else:
                    quote_status = "INQUIRING"

                results.append({
                    "conversation_id": r["conversation_id"],
                    "customer_name": r["customer_name"],
                    "customer_phone": r["customer_phone"],
                    "enquiry_text": r["last_inbound"],
                    "last_intent": r["last_intent"],
                    "products_discussed": products,
                    "selected_sku": r["selected_sku"],
                    "selected_quantity": r["selected_quantity"],
                    "candidate_count": len(cands),
                    "has_pending_quote": bool(pq),
                    "quotation_status": quote_status,
                    "pending_quote": pq,
                    "latest_order_id": r["latest_order_id"],
                    "latest_order_status": r["latest_order_status"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                })
            return results


conversation_service = ConversationService()
