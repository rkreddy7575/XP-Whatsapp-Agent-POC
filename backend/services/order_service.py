import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from services.order_models import Order, OrderItem, OrderStatus
from services.pricing_service import PriceQuoteResult

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


class OrderService:
    """
    Deterministic Order Management Service.
    Persists orders in SQLite, snapshots pricing data immutably, and handles
    customer confirmation flows for WhatsApp sales conversations.
    """

    def __init__(self, db_path: Optional[str] = None):
        self._explicit_db_path = db_path
        # In-memory store for pending customer quotations (keyed by normalized phone)
        self._pending_quotes: Dict[str, PriceQuoteResult] = {}
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._initialized_paths: Set[str] = set()

        if self._explicit_db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            self._init_db(self._mem_conn)
        else:
            self._ensure_initialized()

    @property
    def _is_supabase_primary(self) -> bool:
        """Returns True if Supabase is configured and serves as primary persistence."""
        if self._explicit_db_path == ":memory:":
            return False
        if any("unittest" in str(arg).lower() or "pytest" in str(arg).lower() for arg in sys.argv):
            if os.getenv("USE_SUPABASE_IN_TESTS") not in ("1", "true", "True"):
                return False
        try:
            from services.supabase_repository import SupabaseClient
            return SupabaseClient().is_configured
        except Exception:
            return False

    def _sb_dict_to_order(self, sb_ord: Dict[str, Any]) -> Order:
        """Converts a Supabase order record into an Order domain object."""
        items = [
            OrderItem(
                sku=it.get("sku", ""),
                quantity=int(it.get("quantity", 1)),
                unit_price=float(it.get("unit_price", 0.0)),
                gst_rate=float(it.get("gst_rate", 18.0)),
                gst_amount=float(it.get("gst_amount", 0.0)),
                line_total=float(it.get("line_total", 0.0)),
            )
            for it in sb_ord.get("items", [])
        ]
        status_val = sb_ord.get("status", "CONFIRMED")
        try:
            order_status = OrderStatus(status_val)
        except Exception:
            order_status = OrderStatus.CONFIRMED

        return Order(
            order_id=sb_ord.get("order_id", ""),
            customer_phone=sb_ord.get("customer_phone", ""),
            customer_name=sb_ord.get("customer_name"),
            status=order_status,
            items=items,
            subtotal=float(sb_ord.get("subtotal", 0.0)),
            gst_amount=float(sb_ord.get("gst_amount", 0.0)),
            grand_total=float(sb_ord.get("grand_total", 0.0)),
            currency=sb_ord.get("currency", "INR"),
            created_at=sb_ord.get("created_at", ""),
            updated_at=sb_ord.get("updated_at", ""),
            pricing_version=sb_ord.get("pricing_version", "2025"),
            inventory_status=sb_ord.get("inventory_status", "availability_confirmation_required"),
        )

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
        """Initializes SQLite schema for orders and snapshotted order items."""
        if conn is not None:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    customer_phone TEXT NOT NULL,
                    customer_name TEXT,
                    status TEXT NOT NULL,
                    subtotal REAL NOT NULL,
                    gst_amount REAL NOT NULL,
                    grand_total REAL NOT NULL,
                    currency TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    pricing_version TEXT NOT NULL,
                    inventory_status TEXT NOT NULL
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL,
                    sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    unit_price REAL NOT NULL,
                    gst_rate REAL NOT NULL,
                    gst_amount REAL NOT NULL,
                    line_total REAL NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES orders (order_id)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_orders_customer_phone ON orders (customer_phone);
            """)
            conn.commit()
        else:
            with self._get_connection() as c:
                self._init_db(c)

    def generate_order_id(self) -> str:
        """Generates a human-readable order ID in the format ORD-YYYYMMDD-XXXX."""
        date_str = datetime.now().strftime("%Y%m%d")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM orders WHERE order_id LIKE ?",
                (f"ORD-{date_str}-%",),
            )
            count = cursor.fetchone()[0] + 1
            order_id = f"ORD-{date_str}-{count:04d}"
            # Protect against rare collisions
            while True:
                cursor.execute("SELECT 1 FROM orders WHERE order_id = ?", (order_id,))
                if not cursor.fetchone():
                    break
                count += 1
                order_id = f"ORD-{date_str}-{count:04d}"
            return order_id

    # -------------------------------------------------------------------------
    # Pending Quotation Cache (Session Management)
    # -------------------------------------------------------------------------

    @staticmethod
    def _normalize_phone(phone: Optional[str]) -> str:
        if not phone:
            return ""
        return re.sub(r"[^0-9]", "", str(phone))

    def set_pending_quote(self, customer_phone: str, quote: PriceQuoteResult) -> None:
        """Caches the latest valid quotation for a customer awaiting confirmation."""
        phone_key = self._normalize_phone(customer_phone)
        if phone_key and quote.available:
            self._pending_quotes[phone_key] = quote

    def get_pending_quote(self, customer_phone: str) -> Optional[PriceQuoteResult]:
        """Retrieves the customer's active pending quotation, if any."""
        phone_key = self._normalize_phone(customer_phone)
        return self._pending_quotes.get(phone_key)

    def clear_pending_quote(self, customer_phone: str) -> None:
        """Clears the customer's pending quotation after order creation or reset."""
        phone_key = self._normalize_phone(customer_phone)
        self._pending_quotes.pop(phone_key, None)

    # -------------------------------------------------------------------------
    # Confirmation Detection
    # -------------------------------------------------------------------------

    @staticmethod
    def is_confirmation_intent(text: Optional[str]) -> bool:
        """
        Deterministically checks if customer input is an explicit order confirmation.
        Accepts: CONFIRM, YES, CONFIRMED, PLACE ORDER, OK CONFIRM, etc.
        Rejects: 'maybe', 'check', 'not sure', 'how much', 'change quantity', etc.
        """
        if not text:
            return False

        clean = re.sub(r"[^\w\s]", "", text.strip()).strip().upper()
        if not clean:
            return False

        # Exact matching list
        exact_matches = {
            "CONFIRM",
            "YES",
            "CONFIRMED",
            "PLACE ORDER",
            "PLACE MY ORDER",
            "CONFIRM ORDER",
            "CONFIRM THIS ORDER",
            "YES PLEASE",
            "YES CONFIRM",
            "OK CONFIRM",
            "PROCEED",
            "APPROVE",
        }
        return clean in exact_matches

    # -------------------------------------------------------------------------
    # Order Creation & Storage
    # -------------------------------------------------------------------------

    def create_order_from_quote(
        self,
        customer_phone: str,
        quote: PriceQuoteResult,
        customer_name: Optional[str] = None,
    ) -> Order:
        """
        Creates an immutable Order from an active PriceQuoteResult.
        Stores a frozen price snapshot.
        """
        if not quote.available:
            raise ValueError(f"Cannot create order from an unavailable quotation: {quote.message}")

        if quote.quantity <= 0:
            raise ValueError(f"Order quantity must be positive, got {quote.quantity}")

        if quote.unit_price_excl_gst is None or quote.total_price_incl_gst is None:
            raise ValueError("Quotation missing calculated price fields.")

        order_item = OrderItem(
            sku=quote.sku,
            quantity=quote.quantity,
            unit_price=round(float(quote.unit_price_excl_gst), 2),
            gst_rate=float(quote.gst_percentage or 18.0),
            gst_amount=round(float(quote.total_gst or 0.0), 2),
            line_total=round(float(quote.total_price_incl_gst), 2),
        )

        return self.create_order(
            customer_phone=customer_phone,
            items=[order_item],
            customer_name=customer_name,
            pricing_version=quote.pricing_version or "2025",
        )

    def create_order(
        self,
        customer_phone: str,
        items: List[OrderItem],
        customer_name: Optional[str] = None,
        pricing_version: str = "2025",
    ) -> Order:
        """
        Low-level order creation method supporting multiple order items.
        Persists into SQLite with ACID guarantees.
        """
        if not items:
            raise ValueError("Cannot create an order with zero items.")

        clean_phone = self._normalize_phone(customer_phone)
        if not clean_phone:
            raise ValueError("Valid customer phone number is required.")

        for itm in items:
            if itm.quantity <= 0:
                raise ValueError(f"Item {itm.sku} has invalid quantity {itm.quantity}")
            if itm.unit_price < 0:
                raise ValueError(f"Item {itm.sku} has invalid negative price {itm.unit_price}")

        subtotal = round(sum(itm.unit_price * itm.quantity for itm in items), 2)
        gst_amount = round(sum(itm.gst_amount for itm in items), 2)
        grand_total = round(sum(itm.line_total for itm in items), 2)

        now_iso = datetime.now().isoformat()
        order_id = self.generate_order_id()

        order = Order(
            order_id=order_id,
            customer_phone=clean_phone,
            customer_name=customer_name,
            status=OrderStatus.CONFIRMED,
            items=items,
            subtotal=subtotal,
            gst_amount=gst_amount,
            grand_total=grand_total,
            currency="INR",
            created_at=now_iso,
            updated_at=now_iso,
            pricing_version=pricing_version,
            inventory_status="availability_confirmation_required",
        )

        # Supabase primary write
        if self._is_supabase_primary:
            try:
                from services.supabase_repository import SupabaseClient, SupabaseOrderRepository
                sb = SupabaseClient()
                order_data = {
                    "order_id": order.order_id,
                    "customer_phone": order.customer_phone,
                    "customer_name": order.customer_name,
                    "status": order.status.value,
                    "subtotal": order.subtotal,
                    "gst_amount": order.gst_amount,
                    "grand_total": order.grand_total,
                    "currency": order.currency,
                    "pricing_version": order.pricing_version,
                    "inventory_status": order.inventory_status,
                    "notes": "Created via WhatsApp Agent",
                }
                items_data = [
                    {
                        "sku": itm.sku,
                        "product_name": itm.sku,
                        "quantity": itm.quantity,
                        "unit_price": itm.unit_price,
                        "gst_rate": itm.gst_rate,
                        "gst_amount": itm.gst_amount,
                        "line_total": itm.line_total,
                    }
                    for itm in items
                ]
                SupabaseOrderRepository(sb).create_order(order_data, items_data)
            except Exception:
                pass

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO orders (
                    order_id, customer_phone, customer_name, status, subtotal,
                    gst_amount, grand_total, currency, created_at, updated_at,
                    pricing_version, inventory_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.order_id,
                    order.customer_phone,
                    order.customer_name,
                    order.status.value,
                    order.subtotal,
                    order.gst_amount,
                    order.grand_total,
                    order.currency,
                    order.created_at,
                    order.updated_at,
                    order.pricing_version,
                    order.inventory_status,
                ),
            )
            for itm in items:
                cursor.execute(
                    """
                    INSERT INTO order_items (
                        order_id, sku, quantity, unit_price, gst_rate, gst_amount, line_total
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order.order_id,
                        itm.sku,
                        itm.quantity,
                        itm.unit_price,
                        itm.gst_rate,
                        itm.gst_amount,
                        itm.line_total,
                    ),
                )
            conn.commit()

        return order

    def confirm_pending_order(
        self, customer_phone: str, customer_name: Optional[str] = None
    ) -> Optional[Order]:
        """
        Confirms a customer's active quotation into a persistent order, then clears the cache.
        Returns None if no active quotation exists.
        """
        quote = self.get_pending_quote(customer_phone)
        if not quote:
            return None

        order = self.create_order_from_quote(
            customer_phone=customer_phone,
            quote=quote,
            customer_name=customer_name,
        )
        self.clear_pending_quote(customer_phone)
        return order

    # -------------------------------------------------------------------------
    # Order Retrieval & Management
    # -------------------------------------------------------------------------

    def get_order(self, order_id: str) -> Optional[Order]:
        """Retrieves a single order by order_id, including its items."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
            row = cursor.fetchone()
            if not row:
                try:
                    from services.supabase_repository import SupabaseClient, SupabaseOrderRepository
                    sb = SupabaseClient()
                    if sb.is_configured:
                        sb_ord = SupabaseOrderRepository(sb).get_by_id(order_id)
                        if sb_ord:
                            items = [
                                OrderItem(
                                    sku=it.get("sku", ""),
                                    quantity=it.get("quantity", 1),
                                    unit_price=float(it.get("unit_price", 0.0)),
                                    gst_rate=float(it.get("gst_rate", 18.0)),
                                    gst_amount=float(it.get("gst_amount", 0.0)),
                                    line_total=float(it.get("line_total", 0.0)),
                                )
                                for it in sb_ord.get("items", [])
                            ]
                            status_val = sb_ord.get("status", "CONFIRMED")
                            try:
                                st = OrderStatus(status_val)
                            except Exception:
                                st = OrderStatus.CONFIRMED
                            return Order(
                                order_id=sb_ord.get("order_id", order_id),
                                customer_phone=sb_ord.get("customer_phone", ""),
                                customer_name=sb_ord.get("customer_name"),
                                status=st,
                                items=items,
                                subtotal=float(sb_ord.get("subtotal", 0.0)),
                                gst_amount=float(sb_ord.get("gst_amount", 0.0)),
                                grand_total=float(sb_ord.get("grand_total", 0.0)),
                                currency=sb_ord.get("currency", "INR"),
                                created_at=sb_ord.get("created_at", ""),
                                updated_at=sb_ord.get("updated_at", ""),
                                pricing_version=sb_ord.get("pricing_version", "2025"),
                                inventory_status=sb_ord.get("inventory_status", "CONFIRMED"),
                            )
                except Exception:
                    pass
                return None

            cursor.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,))
            item_rows = cursor.fetchall()

            items = [
                OrderItem(
                    sku=i["sku"],
                    quantity=i["quantity"],
                    unit_price=i["unit_price"],
                    gst_rate=i["gst_rate"],
                    gst_amount=i["gst_amount"],
                    line_total=i["line_total"],
                )
                for i in item_rows
            ]

            return Order(
                order_id=row["order_id"],
                customer_phone=row["customer_phone"],
                customer_name=row["customer_name"],
                status=OrderStatus(row["status"]),
                items=items,
                subtotal=row["subtotal"],
                gst_amount=row["gst_amount"],
                grand_total=row["grand_total"],
                currency=row["currency"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                pricing_version=row["pricing_version"],
                inventory_status=row["inventory_status"],
            )

    def list_customer_orders(self, customer_phone: str) -> List[Order]:
        """Returns all orders placed by a specific customer phone number."""
        clean_phone = self._normalize_phone(customer_phone)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT order_id FROM orders WHERE customer_phone = ? ORDER BY created_at DESC",
                (clean_phone,),
            )
            rows = cursor.fetchall()
            return [self.get_order(r["order_id"]) for r in rows if r["order_id"]]

    def list_all_orders(
        self,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Order]:
        if self._is_supabase_primary:
            try:
                from services.supabase_repository import SupabaseClient, SupabaseOrderRepository
                sb = SupabaseClient()
                sb_orders = SupabaseOrderRepository(sb).list_orders()
                if sb_orders:
                    orders = [self._sb_dict_to_order(o) for o in sb_orders]
                    if status:
                        orders = [o for o in orders if o.status.value == status or o.status == status]
                    if search:
                        q = search.lower()
                        orders = [
                            o for o in orders
                            if q in o.order_id.lower()
                            or q in o.customer_phone.lower()
                            or (o.customer_name and q in o.customer_name.lower())
                        ]
                    return orders
            except Exception:
                pass
        """
        Lists orders with optional filtering by status and search query (order_id, customer_phone, customer_name).
        Sorted newest orders first.
        """
        conditions = []
        params = []

        if status:
            clean_status = status.strip()
            if clean_status:
                conditions.append("status = ?")
                params.append(clean_status)

        if search:
            clean_search = search.strip()
            if clean_search:
                pattern = f"%{clean_search}%"
                conditions.append(
                    "(order_id LIKE ? OR customer_phone LIKE ? OR (customer_name IS NOT NULL AND customer_name LIKE ?))"
                )
                params.extend([pattern, pattern, pattern])

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        sql = f"SELECT order_id FROM orders {where_clause} ORDER BY created_at DESC, order_id DESC"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            results = []
            for r in rows:
                ord_obj = self.get_order(r["order_id"])
                if ord_obj is not None:
                    results.append(ord_obj)
            return results

    def update_order_status(
        self, order_id: str, new_status: OrderStatus
    ) -> Optional[Order]:
        """Updates the status of an existing order."""
        now_iso = datetime.now().isoformat()
        updated_in_sb = False
        if self._is_supabase_primary:
            try:
                from services.supabase_repository import SupabaseClient, SupabaseOrderRepository
                sb = SupabaseClient()
                repo = SupabaseOrderRepository(sb)
                updated_in_sb = repo.update_status(
                    order_id=order_id,
                    new_status=new_status.value,
                    changed_by="dashboard_owner",
                )
            except Exception:
                pass

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?",
                (new_status.value, now_iso, order_id),
            )
            sqlite_updated = cursor.rowcount > 0
            conn.commit()

        if not updated_in_sb and not sqlite_updated:
            return None

        return self.get_order(order_id)
    def cancel_order(self, order_id: str) -> Optional[Order]:
        """Convenience method to cancel an existing order."""
        return self.update_order_status(order_id, OrderStatus.CANCELLED)

    # -------------------------------------------------------------------------
    # Formatting
    # -------------------------------------------------------------------------

    @staticmethod
    def format_order_confirmation(order: Order) -> str:
        """Formats a newly confirmed order into customer-facing WhatsApp markdown."""
        first_item = order.items[0] if order.items else None
        item_desc = (
            f"{first_item.sku} ({first_item.quantity} units)"
            if len(order.items) == 1 and first_item
            else f"{len(order.items)} products ({sum(i.quantity for i in order.items)} units)"
        )

        lines = [
            "✅ *Order Confirmed!*\n",
            f"📋 *Order ID:* `{order.order_id}`",
            f"📦 *Product:* {item_desc}\n",
            f"🧾 *Subtotal:* ₹{order.subtotal:,.2f}",
            f"📊 *Total GST:* ₹{order.gst_amount:,.2f}",
            f"💰 *Grand Total:* ₹{order.grand_total:,.2f}\n",
            "_Our corporate gifting team will contact you shortly regarding order processing and delivery details._",
        ]
        return "\n".join(lines)


# Singleton instance for production use
order_service = OrderService()
