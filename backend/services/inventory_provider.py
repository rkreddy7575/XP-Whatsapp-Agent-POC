from services.catalogue_service import catalogue_service
import json
import logging
import os
import re
import sqlite3
import sys
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("inventory_service")

DEFAULT_PROD_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "app.db")
)
DEFAULT_TEST_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "test_app.db")
)


def get_default_db_path() -> str:
    db_env = os.getenv("DATABASE_PATH")
    if db_env:
        if db_env == ":memory:":
            return ":memory:"
        return os.path.abspath(db_env)
    if os.getenv("TESTING") in ("1", "true", "True") or any(
        "unittest" in str(arg).lower() or "pytest" in str(arg).lower()
        for arg in sys.argv
    ):
        return DEFAULT_TEST_DB_PATH
    return DEFAULT_PROD_DB_PATH


class InventoryStatus(str, Enum):
    IN_STOCK = "IN_STOCK"
    LOW_STOCK = "LOW_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    COMING_SOON = "COMING_SOON"
    SUPPLIER_CONFIRMATION_REQUIRED = "SUPPLIER_CONFIRMATION_REQUIRED"
    UNKNOWN = "UNKNOWN"


class StockAction(str, Enum):
    RECEIVE = "RECEIVE"
    ADD = "ADD"
    REMOVE = "REMOVE"
    CORRECTION = "CORRECTION"
    DAMAGED = "DAMAGED"
    SET = "SET"


@dataclass
class InventoryItem:
    """Represents the inventory state of a product SKU."""
    sku: str
    physical_stock: Optional[int] = None
    reserved_stock: int = 0
    reorder_level: Optional[int] = 100
    unit_cost: Optional[float] = None
    status: InventoryStatus = InventoryStatus.UNKNOWN
    supplier_id: Optional[str] = None
    source: str = "SQLITE_PERSISTED"
    last_updated: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    is_mock: bool = False

    def __post_init__(self):
        if self.physical_stock is not None and self.physical_stock < 0:
            raise ValueError(f"physical_stock cannot be negative for {self.sku}")
        if self.reserved_stock < 0:
            raise ValueError(f"reserved_stock cannot be negative for {self.sku}")
        if self.physical_stock is not None and self.reserved_stock > self.physical_stock:
            raise ValueError(f"reserved_stock ({self.reserved_stock}) cannot exceed physical_stock ({self.physical_stock}) for {self.sku}")

    @property
    def available_stock(self) -> int:
        """Available stock = physical_stock - reserved_stock. Never negative. Returns 0 if physical_stock is None."""
        if self.physical_stock is None:
            return 0
        return max(0, self.physical_stock - self.reserved_stock)

    @property
    def raw_available(self) -> Optional[int]:
        if self.physical_stock is None:
            return None
        return max(0, self.physical_stock - self.reserved_stock)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sku": self.sku,
            "name": self.name,
            "category": self.category,
            "physical_stock": self.physical_stock,
            "reserved_stock": self.reserved_stock,
            "available_stock": self.raw_available,
            "reorder_level": self.reorder_level,
            "unit_cost": self.unit_cost,
            "status": self.status.value if isinstance(self.status, InventoryStatus) else str(self.status),
            "last_updated": self.last_updated,
            "supplier_id": self.supplier_id,
            "source": self.source,
            "is_mock": self.is_mock,
        }


@dataclass
class InventoryTransaction:
    id: str
    sku: str
    transaction_type: str
    quantity_change: int
    quantity_before: Optional[int]
    quantity_after: Optional[int]
    reason: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    notes: Optional[str] = None
    created_by: str = "owner"
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AvailabilityResult:
    """Result of checking stock availability for a given SKU and requested quantity."""
    available: bool
    sku: str
    requested_quantity: int
    available_stock: int
    status: InventoryStatus
    is_live: bool
    message: str


def normalize_sku_key(sku: str) -> str:
    """Normalizes an SKU for consistent dictionary lookups."""
    if not sku:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(sku).upper())


class InventoryProvider(ABC):
    """Abstract interface for inventory providers."""

    @abstractmethod
    def is_live(self) -> bool:
        pass

    @abstractmethod
    def get_inventory(self, sku: str) -> Optional[InventoryItem]:
        pass

    @abstractmethod
    def get_all_inventory_map(self, tenant_id: str = "default") -> Dict[str, InventoryItem]:
        pass

    @abstractmethod
    def check_availability(self, sku: str, requested_quantity: int) -> AvailabilityResult:
        pass

    @abstractmethod
    def get_available_quantity(self, sku: str) -> int:
        pass

    @abstractmethod
    def reserve_stock(self, sku: str, quantity: int) -> bool:
        pass

    @abstractmethod
    def release_stock(self, sku: str, quantity: int) -> bool:
        pass


class DevelopmentInventoryProvider(InventoryProvider):
    """
    In-memory development and testing inventory provider.
    Loads initial data from backend/data/inventory_mock.json or in-memory list.
    """

    def __init__(self, data_path: Optional[str] = None, initial_items: Optional[List[InventoryItem]] = None):
        self._items: Dict[str, InventoryItem] = {}
        if initial_items is not None:
            for itm in initial_items:
                self._add_item(itm)
        else:
            if data_path is None:
                data_path = os.path.join(os.path.dirname(__file__), "..", "data", "inventory_mock.json")
            self._load_from_json(data_path)

    def is_live(self) -> bool:
        return False

    def _add_item(self, item: InventoryItem) -> None:
        if item.physical_stock is not None and item.physical_stock < 0:
            raise ValueError(f"physical_stock cannot be negative for {item.sku}")
        if item.reserved_stock < 0:
            raise ValueError(f"reserved_stock cannot be negative for {item.sku}")
        if item.physical_stock is not None and item.reserved_stock > item.physical_stock:
            raise ValueError(f"reserved_stock ({item.reserved_stock}) cannot exceed physical_stock ({item.physical_stock}) for {item.sku}")

        key = normalize_sku_key(item.sku)
        self._items[key] = item

    def _load_from_json(self, data_path: str) -> None:
        data_path = os.path.abspath(data_path)
        if not os.path.exists(data_path):
            return

        with open(data_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        items_list = raw_data.get("items", [])
        for entry in items_list:
            raw_status = entry.get("status", "IN_STOCK")
            try:
                status_enum = InventoryStatus(raw_status)
            except ValueError:
                status_enum = InventoryStatus.UNKNOWN

            item = InventoryItem(
                sku=entry["sku"],
                physical_stock=int(entry["physical_stock"]),
                reserved_stock=int(entry["reserved_stock"]),
                reorder_level=entry.get("reorder_level"),
                status=status_enum,
                supplier_id=entry.get("supplier_id"),
                source=entry.get("source", "DEVELOPMENT_MOCK"),
                last_updated=entry.get("last_updated"),
                is_mock=True,
            )
            self._add_item(item)

    def get_inventory(self, sku: str) -> Optional[InventoryItem]:
        key = normalize_sku_key(sku)
        return self._items.get(key)

    def get_all_inventory_map(self, tenant_id: str = "default") -> Dict[str, InventoryItem]:
        return {k: v for k, v in self._items.items()}

    def get_available_quantity(self, sku: str) -> int:
        item = self.get_inventory(sku)
        return item.available_stock if item else 0

    def check_availability(self, sku: str, requested_quantity: int) -> AvailabilityResult:
        if requested_quantity <= 0:
            return AvailabilityResult(
                available=False,
                sku=sku,
                requested_quantity=requested_quantity,
                available_stock=0,
                status=InventoryStatus.UNKNOWN,
                is_live=False,
                message="Requested quantity must be greater than zero.",
            )

        item = self.get_inventory(sku)
        if not item:
            return AvailabilityResult(
                available=False,
                sku=sku,
                requested_quantity=requested_quantity,
                available_stock=0,
                status=InventoryStatus.UNKNOWN,
                is_live=False,
                message=f"No inventory record found for SKU {sku}.",
            )

        avail = item.available_stock
        is_sufficient = avail >= requested_quantity

        if is_sufficient:
            msg = f"{requested_quantity} units available in mock development stock."
        else:
            msg = f"Insufficient stock: {requested_quantity} requested, only {avail} available in mock development stock."

        return AvailabilityResult(
            available=is_sufficient,
            sku=item.sku,
            requested_quantity=requested_quantity,
            available_stock=avail,
            status=item.status,
            is_live=False,
            message=msg,
        )

    def reserve_stock(self, sku: str, quantity: int) -> bool:
        if quantity <= 0:
            return False

        item = self.get_inventory(sku)
        if not item:
            return False

        if quantity > item.available_stock:
            return False

        item.reserved_stock += quantity
        item.last_updated = datetime.now().isoformat()
        return True

    def release_stock(self, sku: str, quantity: int) -> bool:
        if quantity <= 0:
            return False

        item = self.get_inventory(sku)
        if not item:
            return False

        if quantity > item.reserved_stock:
            return False

        item.reserved_stock -= quantity
        item.last_updated = datetime.now().isoformat()
        return True


class SqliteInventoryProvider(InventoryProvider):
    """
    Persistent SQLite inventory provider.
    Maintains inventory table and immutable inventory_transactions log.
    Ensures unknown inventory is distinct from out-of-stock.
    Scoped by tenant_id (default 'default').
    """

    def __init__(self, db_path: Optional[str] = None, tenant_id: str = "default"):
        self._db_path = db_path or get_default_db_path()
        self.tenant_id = tenant_id
        self._mem_conn: Optional[sqlite3.Connection] = None
        if self._db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            self._init_db(self._mem_conn)
        else:
            self._ensure_initialized()

    def is_live(self) -> bool:
        return False

    def _get_connection(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        if self._db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            self._init_db(self._mem_conn)
            return self._mem_conn
        dir_name = os.path.dirname(self._db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_initialized(self) -> None:
        conn = self._get_connection()
        try:
            self._init_db(conn)
        finally:
            if self._mem_conn is None:
                conn.close()

    def _init_db(self, conn: sqlite3.Connection) -> None:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inventory (
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    sku TEXT NOT NULL,
                    physical_quantity INTEGER,
                    reserved_quantity INTEGER NOT NULL DEFAULT 0,
                    reorder_level INTEGER NOT NULL DEFAULT 100,
                    unit_cost REAL,
                    status TEXT NOT NULL DEFAULT 'UNKNOWN',
                    supplier_id TEXT,
                    last_updated TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, sku)
                )
                """
            )
            try:
                cur = conn.cursor()
                cur.execute("PRAGMA table_info(inventory);")
                cols = [c["name"] for c in cur.fetchall()]
                if "tenant_id" not in cols:
                    conn.execute("ALTER TABLE inventory ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';")
                if "supplier_id" not in cols:
                    conn.execute("ALTER TABLE inventory ADD COLUMN supplier_id TEXT;")
            except Exception:
                pass

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inventory_transactions (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    sku TEXT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    quantity_change INTEGER NOT NULL,
                    quantity_before INTEGER,
                    quantity_after INTEGER,
                    reason TEXT,
                    reference_type TEXT,
                    reference_id TEXT,
                    notes TEXT,
                    created_by TEXT NOT NULL DEFAULT 'owner',
                    created_at TEXT NOT NULL
                )
                """
            )
            try:
                cur = conn.cursor()
                cur.execute("PRAGMA table_info(inventory_transactions);")
                cols = [c["name"] for c in cur.fetchall()]
                if "tenant_id" not in cols:
                    conn.execute("ALTER TABLE inventory_transactions ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';")
            except Exception:
                pass

            conn.execute("CREATE INDEX IF NOT EXISTS idx_inv_tx_tenant_sku ON inventory_transactions(tenant_id, sku)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_inv_tx_created_at ON inventory_transactions(tenant_id, created_at DESC)")

            # Seed from inventory_mock.json if table is empty
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM inventory WHERE tenant_id = ?", (self.tenant_id,))
            if cur.fetchone()[0] == 0:
                mock_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "inventory_mock.json"))
                if os.path.exists(mock_path):
                    try:
                        with open(mock_path, "r", encoding="utf-8") as f:
                            raw = json.load(f)
                        for item in raw.get("items", []):
                            norm_sku = item["sku"].strip().upper()
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO inventory
                                (tenant_id, sku, physical_quantity, reserved_quantity, reorder_level, status, last_updated)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    self.tenant_id,
                                    norm_sku,
                                    int(item.get("physical_stock", 0)),
                                    int(item.get("reserved_stock", 0)),
                                    int(item.get("reorder_level", 100)),
                                    item.get("status", "IN_STOCK"),
                                    item.get("last_updated", datetime.now().isoformat()),
                                )
                            )
                    except Exception as e:
                        logger.warning(f"Failed seeding inventory from mock: {e}")

    def _compute_status(self, physical: Optional[int], reserved: int, reorder_level: int, current_status: str) -> InventoryStatus:
        if current_status in (
            InventoryStatus.COMING_SOON.value,
            InventoryStatus.SUPPLIER_CONFIRMATION_REQUIRED.value,
        ):
            return InventoryStatus(current_status)

        if physical is None:
            return InventoryStatus.UNKNOWN

        available = max(0, physical - reserved)
        if available == 0:
            return InventoryStatus.OUT_OF_STOCK
        if available <= reorder_level:
            return InventoryStatus.LOW_STOCK
        return InventoryStatus.IN_STOCK

    def get_inventory(self, sku: str) -> Optional[InventoryItem]:
        norm_sku = sku.strip().upper()
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM inventory WHERE tenant_id = ? AND UPPER(sku) = ?",
                (self.tenant_id, norm_sku),
            )
            row = cur.fetchone()
            if not row:
                return None
            st_val = row["status"]
            try:
                st = InventoryStatus(st_val)
            except ValueError:
                st = InventoryStatus.UNKNOWN
            return InventoryItem(
                sku=row["sku"],
                physical_stock=row["physical_quantity"],
                reserved_stock=row["reserved_quantity"] or 0,
                reorder_level=row["reorder_level"] if row["reorder_level"] is not None else 100,
                unit_cost=row["unit_cost"],
                status=st,
                supplier_id=row["supplier_id"] if "supplier_id" in row.keys() else None,
                source="SQLITE_PERSISTED",
                last_updated=row["last_updated"],
            )
        finally:
            if self._mem_conn is None:
                conn.close()

    def get_all_inventory_map(self, tenant_id: Optional[str] = None) -> Dict[str, InventoryItem]:
        target_tenant = tenant_id or self.tenant_id
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM inventory WHERE tenant_id = ?", (target_tenant,))
            rows = cur.fetchall()
            result = {}
            for r in rows:
                norm_sku = r["sku"].strip().upper()
                st_val = r["status"]
                try:
                    st = InventoryStatus(st_val)
                except ValueError:
                    st = InventoryStatus.UNKNOWN
                result[norm_sku] = InventoryItem(
                    sku=norm_sku,
                    physical_stock=r["physical_quantity"],
                    reserved_stock=r["reserved_quantity"] or 0,
                    reorder_level=r["reorder_level"] if r["reorder_level"] is not None else 100,
                    unit_cost=r["unit_cost"],
                    status=st,
                    supplier_id=r["supplier_id"] if "supplier_id" in r.keys() else None,
                    source="SQLITE_PERSISTED",
                    last_updated=r["last_updated"],
                )
            return result
        finally:
            if self._mem_conn is None:
                conn.close()

    def get_or_create_inventory(self, sku: str) -> InventoryItem:
        item = self.get_inventory(sku)
        if item is not None:
            return item
        norm_sku = sku.strip().upper()
        return InventoryItem(
            sku=norm_sku,
            physical_stock=None,
            reserved_stock=0,
            reorder_level=100,
            status=InventoryStatus.UNKNOWN,
            source="SQLITE_PERSISTED",
            last_updated=datetime.now().isoformat(),
        )

    def check_availability(self, sku: str, requested_quantity: int) -> AvailabilityResult:
        item = self.get_inventory(sku)
        if item is None or item.physical_stock is None:
            return AvailabilityResult(
                available=False,
                sku=sku,
                requested_quantity=requested_quantity,
                available_stock=0,
                status=InventoryStatus.UNKNOWN,
                is_live=self.is_live(),
                message=f"Stock level unknown for SKU {sku}. Owner verification required.",
            )
        avail = item.available_stock
        is_avail = avail >= requested_quantity
        return AvailabilityResult(
            available=is_avail,
            sku=sku,
            requested_quantity=requested_quantity,
            available_stock=avail,
            status=item.status,
            is_live=self.is_live(),
            message="Stock available" if is_avail else f"Insufficient stock. Only {avail} available.",
        )

    def get_available_quantity(self, sku: str) -> int:
        item = self.get_inventory(sku)
        return item.available_stock if item else 0

    def reserve_stock(self, sku: str, quantity: int) -> bool:
        if quantity <= 0:
            return True
        norm_sku = sku.strip().upper()
        conn = self._get_connection()
        try:
            with conn:
                item = self.get_or_create_inventory(norm_sku)
                if item.physical_stock is None:
                    return False
                if item.available_stock < quantity:
                    return False
                new_reserved = item.reserved_stock + quantity
                new_status = self._compute_status(item.physical_stock, new_reserved, item.reorder_level or 100, item.status.value)
                now_str = datetime.now().isoformat()
                conn.execute(
                    """
                    INSERT INTO inventory (tenant_id, sku, physical_quantity, reserved_quantity, reorder_level, unit_cost, status, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, sku) DO UPDATE SET
                        reserved_quantity = excluded.reserved_quantity,
                        status = excluded.status,
                        last_updated = excluded.last_updated
                    """,
                    (self.tenant_id, norm_sku, item.physical_stock, new_reserved, item.reorder_level, item.unit_cost, new_status.value, now_str)
                )
                return True
        finally:
            if self._mem_conn is None:
                conn.close()

    def release_stock(self, sku: str, quantity: int) -> bool:
        if quantity <= 0:
            return True
        norm_sku = sku.strip().upper()
        conn = self._get_connection()
        try:
            with conn:
                item = self.get_inventory(norm_sku)
                if not item:
                    return True
                new_reserved = max(0, item.reserved_stock - quantity)
                new_status = self._compute_status(item.physical_stock, new_reserved, item.reorder_level or 100, item.status.value)
                now_str = datetime.now().isoformat()
                conn.execute(
                    """
                    UPDATE inventory
                    SET reserved_quantity = ?, status = ?, last_updated = ?
                    WHERE tenant_id = ? AND UPPER(sku) = UPPER(?)
                    """,
                    (new_reserved, new_status.value, now_str, self.tenant_id, norm_sku)
                )
                return True
        finally:
            if self._mem_conn is None:
                conn.close()

    def adjust_stock(
        self,
        sku: str,
        action: StockAction,
        quantity: int,
        reason: Optional[str] = None,
        notes: Optional[str] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        user: str = "owner",
        unit_cost: Optional[float] = None,
    ) -> InventoryItem:
        norm_sku = sku.strip().upper()
        conn = self._get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT * FROM inventory WHERE tenant_id = ? AND UPPER(sku) = ?",
                    (self.tenant_id, norm_sku),
                )
                row = cur.fetchone()

                curr_physical = row["physical_quantity"] if row else None
                curr_reserved = row["reserved_quantity"] if (row and row["reserved_quantity"]) else 0
                reorder = row["reorder_level"] if (row and row["reorder_level"] is not None) else 100
                cost = unit_cost if unit_cost is not None else (row["unit_cost"] if row else None)
                curr_status_str = row["status"] if row else "UNKNOWN"
                supp_id = row["supplier_id"] if (row and "supplier_id" in row.keys()) else None

                base_val = curr_physical if curr_physical is not None else 0

                if action in (StockAction.RECEIVE, StockAction.ADD):
                    new_physical = base_val + max(0, quantity)
                    tx_type = "STOCK_RECEIVED" if action == StockAction.RECEIVE else "STOCK_ADDED"
                    qty_change = max(0, quantity)
                elif action == StockAction.REMOVE:
                    new_physical = max(0, base_val - max(0, quantity))
                    tx_type = "STOCK_REMOVED"
                    qty_change = -(base_val - new_physical)
                elif action == StockAction.DAMAGED:
                    new_physical = max(0, base_val - max(0, quantity))
                    tx_type = "DAMAGED"
                    qty_change = -(base_val - new_physical)
                elif action in (StockAction.CORRECTION, StockAction.SET):
                    new_physical = max(0, quantity)
                    tx_type = "STOCK_ADJUSTMENT"
                    qty_change = new_physical - base_val
                else:
                    new_physical = max(0, quantity)
                    tx_type = "STOCK_ADJUSTMENT"
                    qty_change = new_physical - base_val

                new_status = self._compute_status(new_physical, curr_reserved, reorder, curr_status_str)
                now_str = datetime.now().isoformat()

                conn.execute(
                    """
                    INSERT INTO inventory (tenant_id, sku, physical_quantity, reserved_quantity, reorder_level, unit_cost, status, supplier_id, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, sku) DO UPDATE SET
                        physical_quantity = excluded.physical_quantity,
                        reserved_quantity = excluded.reserved_quantity,
                        reorder_level = excluded.reorder_level,
                        unit_cost = COALESCE(excluded.unit_cost, inventory.unit_cost),
                        status = excluded.status,
                        supplier_id = COALESCE(excluded.supplier_id, inventory.supplier_id),
                        last_updated = excluded.last_updated
                    """,
                    (self.tenant_id, norm_sku, new_physical, curr_reserved, reorder, cost, new_status.value, supp_id, now_str)
                )

                tx_id = f"tx_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO inventory_transactions (
                        id, tenant_id, sku, transaction_type, quantity_change,
                        quantity_before, quantity_after, reason, reference_type,
                        reference_id, notes, created_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tx_id, self.tenant_id, norm_sku, tx_type, qty_change,
                        curr_physical, new_physical, reason or "Stock adjustment",
                        reference_type or "MANUAL", reference_id, notes, user, now_str
                    )
                )

                return InventoryItem(
                    sku=norm_sku,
                    physical_stock=new_physical,
                    reserved_stock=curr_reserved,
                    reorder_level=reorder,
                    unit_cost=cost,
                    status=new_status,
                    supplier_id=supp_id,
                    source="SQLITE_PERSISTED",
                    last_updated=now_str,
                )
        finally:
            if self._mem_conn is None:
                conn.close()

    def update_status(
        self,
        sku: str,
        status: Any,
        reason: Optional[str] = None,
        user: str = "owner",
    ) -> InventoryItem:
        norm_sku = sku.strip().upper()
        if isinstance(status, str):
            try:
                status_enum = InventoryStatus(status)
            except ValueError:
                status_enum = InventoryStatus.UNKNOWN
            status_val = status
        else:
            status_enum = status
            status_val = status.value

        conn = self._get_connection()
        try:
            with conn:
                item = self.get_inventory(norm_sku)
                curr_phys = item.physical_stock if item else None
                curr_res = item.reserved_stock if item else 0
                reorder = item.reorder_level if item else 100
                cost = item.unit_cost if item else None
                supp_id = item.supplier_id if item else None
                now_str = datetime.now().isoformat()

                conn.execute(
                    """
                    INSERT INTO inventory (tenant_id, sku, physical_quantity, reserved_quantity, reorder_level, unit_cost, status, supplier_id, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, sku) DO UPDATE SET
                        status = excluded.status,
                        last_updated = excluded.last_updated
                    """,
                    (self.tenant_id, norm_sku, curr_phys, curr_res, reorder, cost, status_val, supp_id, now_str)
                )

                tx_id = f"tx_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO inventory_transactions (
                        id, tenant_id, sku, transaction_type, quantity_change,
                        quantity_before, quantity_after, reason, reference_type,
                        reference_id, notes, created_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tx_id, self.tenant_id, norm_sku, "STATUS_CHANGE", 0,
                        curr_phys, curr_phys, reason or f"Status changed to {status_val}",
                        "MANUAL", None, None, user, now_str
                    )
                )

                return InventoryItem(
                    sku=norm_sku,
                    physical_stock=curr_phys,
                    reserved_stock=curr_res,
                    reorder_level=reorder,
                    unit_cost=cost,
                    status=status_enum,
                    supplier_id=supp_id,
                    source="SQLITE_PERSISTED",
                    last_updated=now_str,
                )
        finally:
            if self._mem_conn is None:
                conn.close()

    def update_reorder_level(
        self,
        sku: str,
        reorder_level: int,
        reason: Optional[str] = None,
        user: str = "owner",
    ) -> InventoryItem:
        norm_sku = sku.strip().upper()
        conn = self._get_connection()
        try:
            with conn:
                item = self.get_inventory(norm_sku)
                curr_phys = item.physical_stock if item else None
                curr_res = item.reserved_stock if item else 0
                cost = item.unit_cost if item else None
                supp_id = item.supplier_id if item else None
                curr_status_str = item.status.value if item else "UNKNOWN"

                new_status = self._compute_status(curr_phys, curr_res, reorder_level, curr_status_str)
                now_str = datetime.now().isoformat()

                conn.execute(
                    """
                    INSERT INTO inventory (tenant_id, sku, physical_quantity, reserved_quantity, reorder_level, unit_cost, status, supplier_id, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, sku) DO UPDATE SET
                        reorder_level = excluded.reorder_level,
                        status = excluded.status,
                        last_updated = excluded.last_updated
                    """,
                    (self.tenant_id, norm_sku, curr_phys, curr_res, reorder_level, cost, new_status.value, supp_id, now_str)
                )

                tx_id = f"tx_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO inventory_transactions (
                        id, tenant_id, sku, transaction_type, quantity_change,
                        quantity_before, quantity_after, reason, reference_type,
                        reference_id, notes, created_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tx_id, self.tenant_id, norm_sku, "REORDER_LEVEL_CHANGE", 0,
                        curr_phys, curr_phys, reason or f"Reorder level set to {reorder_level}",
                        "MANUAL", None, None, user, now_str
                    )
                )

                return InventoryItem(
                    sku=norm_sku,
                    physical_stock=curr_phys,
                    reserved_stock=curr_res,
                    reorder_level=reorder_level,
                    unit_cost=cost,
                    status=new_status,
                    supplier_id=supp_id,
                    source="SQLITE_PERSISTED",
                    last_updated=now_str,
                )
        finally:
            if self._mem_conn is None:
                conn.close()

    def get_transactions(
        self,
        sku: Optional[str] = None,
        limit: int = 50,
    ) -> List[InventoryTransaction]:
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            if sku:
                norm_sku = sku.strip().upper()
                cur.execute(
                    """
                    SELECT * FROM inventory_transactions
                    WHERE tenant_id = ? AND UPPER(sku) = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (self.tenant_id, norm_sku, limit)
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM inventory_transactions
                    WHERE tenant_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (self.tenant_id, limit)
                )
            rows = cur.fetchall()
            txs = []
            for r in rows:
                txs.append(
                    InventoryTransaction(
                        id=r["id"],
                        sku=r["sku"],
                        transaction_type=r["transaction_type"],
                        quantity_change=r["quantity_change"],
                        quantity_before=r["quantity_before"],
                        quantity_after=r["quantity_after"],
                        reason=r["reason"],
                        reference_type=r["reference_type"],
                        reference_id=r["reference_id"],
                        notes=r["notes"],
                        created_by=r["created_by"],
                        created_at=r["created_at"],
                    )
                )
            return txs
        finally:
            if self._mem_conn is None:
                conn.close()


# =============================================================================
# Supabase Production Inventory Provider
# =============================================================================
class SupabaseInventoryProvider(InventoryProvider):
    """
    Durable Supabase production inventory provider.
    Maintains inventory table and immutable inventory_transactions log in Supabase.
    Ensures multi-tenant logical keys (tenant_id + sku).
    Ensures unknown inventory is distinct from out-of-stock.
    """

    def __init__(self, repo: Optional[Any] = None, tenant_id: str = "default"):
        self.tenant_id = tenant_id
        if repo is not None:
            self._repo = repo
        else:
            from services.supabase_repository import SupabaseInventoryRepository
            self._repo = SupabaseInventoryRepository(tenant_id=tenant_id)

    @property
    def repo(self) -> Any:
        return self._repo

    @property
    def is_configured(self) -> bool:
        return getattr(self._repo, "is_configured", False)

    def is_live(self) -> bool:
        return True

    def _compute_status(
        self,
        physical: Optional[int],
        reserved: int,
        reorder_level: int,
        current_status: str,
    ) -> InventoryStatus:
        if current_status in (
            InventoryStatus.COMING_SOON.value,
            InventoryStatus.SUPPLIER_CONFIRMATION_REQUIRED.value,
        ):
            return InventoryStatus(current_status)

        if physical is None:
            return InventoryStatus.UNKNOWN

        available = max(0, physical - reserved)
        if available == 0:
            return InventoryStatus.OUT_OF_STOCK
        if available <= reorder_level:
            return InventoryStatus.LOW_STOCK
        return InventoryStatus.IN_STOCK

    def get_inventory(self, sku: str) -> Optional[InventoryItem]:
        row = self._repo.get_inventory(sku)
        if not row:
            return None
        norm_sku = sku.strip().upper()
        prod = catalogue_service.sku_index.get(norm_sku)
        st_val = row.get("status", "UNKNOWN")
        try:
            status_enum = InventoryStatus(st_val)
        except ValueError:
            status_enum = InventoryStatus.UNKNOWN
        return InventoryItem(
            sku=norm_sku,
            physical_stock=row.get("physical_quantity"),
            reserved_stock=row.get("reserved_quantity", 0) or 0,
            reorder_level=row.get("reorder_level", 100) if row.get("reorder_level") is not None else 100,
            unit_cost=row.get("unit_cost"),
            status=status_enum,
            supplier_id=row.get("supplier_id"),
            source="SUPABASE_PERSISTED",
            last_updated=row.get("last_updated"),
            name=prod.get("name") if prod else None,
            category=prod.get("category") if prod else None,
        )

    def get_all_inventory_map(self, tenant_id: Optional[str] = None) -> Dict[str, InventoryItem]:
        target_tenant = tenant_id or self.tenant_id
        rows_map = self._repo.get_all_inventory_map(tenant_id=target_tenant)
        result: Dict[str, InventoryItem] = {}
        for norm_sku, row in rows_map.items():
            prod = catalogue_service.sku_index.get(norm_sku)
            st_val = row.get("status", "UNKNOWN")
            try:
                status_enum = InventoryStatus(st_val)
            except ValueError:
                status_enum = InventoryStatus.UNKNOWN
            result[norm_sku] = InventoryItem(
                sku=norm_sku,
                physical_stock=row.get("physical_quantity"),
                reserved_stock=row.get("reserved_quantity", 0) or 0,
                reorder_level=row.get("reorder_level", 100) if row.get("reorder_level") is not None else 100,
                unit_cost=row.get("unit_cost"),
                status=status_enum,
                supplier_id=row.get("supplier_id"),
                source="SUPABASE_PERSISTED",
                last_updated=row.get("last_updated"),
                name=prod.get("name") if prod else None,
                category=prod.get("category") if prod else None,
            )
        return result

    def get_or_create_inventory(self, sku: str) -> InventoryItem:
        existing = self.get_inventory(sku)
        if existing:
            return existing
        norm_sku = sku.strip().upper()
        prod = catalogue_service.sku_index.get(norm_sku)
        self._repo.upsert_inventory_item(
            sku=norm_sku,
            physical_quantity=None,
            reserved_quantity=0,
            reorder_level=100,
            status="UNKNOWN",
        )
        return InventoryItem(
            sku=norm_sku,
            physical_stock=None,
            reserved_stock=0,
            reorder_level=100,
            status=InventoryStatus.UNKNOWN,
            source="SUPABASE_PERSISTED",
            name=prod.get("name") if prod else None,
            category=prod.get("category") if prod else None,
        )

    def check_availability(self, sku: str, requested_quantity: int) -> AvailabilityResult:
        item = self.get_inventory(sku)
        if item is None or item.status == InventoryStatus.UNKNOWN or item.physical_stock is None:
            return AvailabilityResult(
                available=False,
                sku=sku,
                requested_quantity=requested_quantity,
                available_stock=0,
                status=InventoryStatus.UNKNOWN,
                is_live=self.is_live(),
                message=f"Stock level unknown for SKU {sku}. Owner verification required.",
            )
        avail = item.available_stock
        is_avail = avail >= requested_quantity
        return AvailabilityResult(
            available=is_avail,
            sku=sku,
            requested_quantity=requested_quantity,
            available_stock=avail,
            status=item.status,
            is_live=self.is_live(),
            message="Stock available" if is_avail else f"Insufficient stock. Only {avail} available.",
        )

    def get_available_quantity(self, sku: str) -> int:
        item = self.get_inventory(sku)
        return item.available_stock if item else 0

    def reserve_stock(self, sku: str, quantity: int) -> bool:
        if quantity <= 0:
            return True
        norm_sku = sku.strip().upper()
        item = self.get_inventory(norm_sku)
        if not item or item.physical_stock is None:
            return False
        avail = item.available_stock
        if avail < quantity:
            return False
        new_reserved = item.reserved_stock + quantity
        new_status = self._compute_status(item.physical_stock, new_reserved, item.reorder_level or 100, item.status.value)
        self._repo.upsert_inventory_item(
            sku=item.sku,
            physical_quantity=item.physical_stock,
            reserved_quantity=new_reserved,
            reorder_level=item.reorder_level or 100,
            unit_cost=item.unit_cost,
            status=new_status.value,
        )
        return True

    def release_stock(self, sku: str, quantity: int) -> bool:
        if quantity <= 0:
            return True
        norm_sku = sku.strip().upper()
        item = self.get_inventory(norm_sku)
        if not item:
            return True
        new_reserved = max(0, item.reserved_stock - quantity)
        new_status = self._compute_status(item.physical_stock, new_reserved, item.reorder_level or 100, item.status.value)
        self._repo.upsert_inventory_item(
            sku=item.sku,
            physical_quantity=item.physical_stock,
            reserved_quantity=new_reserved,
            reorder_level=item.reorder_level or 100,
            unit_cost=item.unit_cost,
            status=new_status.value,
        )
        return True

    def adjust_stock(
        self,
        sku: str,
        action: StockAction,
        quantity: int,
        reason: Optional[str] = None,
        notes: Optional[str] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        user: str = "owner",
        unit_cost: Optional[float] = None,
    ) -> InventoryItem:
        norm_sku = sku.strip().upper()
        item = self.get_inventory(norm_sku)

        curr_physical = item.physical_stock if item else None
        curr_reserved = item.reserved_stock if item else 0
        curr_reorder = item.reorder_level if item else 100
        curr_cost = item.unit_cost if item else None
        curr_status_str = item.status.value if item else "UNKNOWN"
        supp_id = item.supplier_id if item else None

        base_val = curr_physical if curr_physical is not None else 0

        if action in (StockAction.RECEIVE, StockAction.ADD):
            new_physical = base_val + max(0, quantity)
            tx_type = "STOCK_RECEIVED" if action == StockAction.RECEIVE else "STOCK_ADDED"
            qty_change = max(0, quantity)
        elif action == StockAction.REMOVE:
            new_physical = max(0, base_val - max(0, quantity))
            tx_type = "STOCK_REMOVED"
            qty_change = -(base_val - new_physical)
        elif action == StockAction.DAMAGED:
            new_physical = max(0, base_val - max(0, quantity))
            tx_type = "DAMAGED"
            qty_change = -(base_val - new_physical)
        elif action in (StockAction.CORRECTION, StockAction.SET):
            new_physical = max(0, quantity)
            tx_type = "STOCK_ADJUSTMENT"
            qty_change = new_physical - base_val
        else:
            new_physical = max(0, quantity)
            tx_type = "STOCK_ADJUSTMENT"
            qty_change = new_physical - base_val

        new_status = self._compute_status(new_physical, curr_reserved, curr_reorder, curr_status_str)
        eff_cost = unit_cost if unit_cost is not None else curr_cost

        # Persist to Supabase
        self._repo.upsert_inventory_item(
            sku=norm_sku,
            physical_quantity=new_physical,
            reserved_quantity=curr_reserved,
            reorder_level=curr_reorder,
            unit_cost=eff_cost,
            status=new_status.value,
            supplier_id=supp_id,
        )

        # Record transaction audit
        self._repo.record_transaction(
            sku=norm_sku,
            transaction_type=tx_type,
            quantity_change=qty_change,
            quantity_before=curr_physical,
            quantity_after=new_physical,
            reason=reason or "Stock adjustment",
            reference_type=reference_type or "MANUAL",
            reference_id=reference_id,
            notes=notes,
            created_by=user,
        )

        prod = catalogue_service.sku_index.get(norm_sku)
        now_str = datetime.now().isoformat()
        return InventoryItem(
            sku=norm_sku,
            physical_stock=new_physical,
            reserved_stock=curr_reserved,
            reorder_level=curr_reorder,
            unit_cost=eff_cost,
            status=new_status,
            supplier_id=supp_id,
            source="SUPABASE_PERSISTED",
            last_updated=now_str,
            name=prod.get("name") if prod else None,
            category=prod.get("category") if prod else None,
        )

    def update_status(
        self,
        sku: str,
        status: InventoryStatus,
        reason: Optional[str] = None,
        user: str = "owner",
    ) -> InventoryItem:
        norm_sku = sku.strip().upper()
        item = self.get_inventory(norm_sku)
        curr_phys = item.physical_stock if item else None
        curr_res = item.reserved_stock if item else 0
        curr_reorder = item.reorder_level if item else 100
        curr_cost = item.unit_cost if item else None
        supp_id = item.supplier_id if item else None

        self._repo.upsert_inventory_item(
            sku=norm_sku,
            physical_quantity=curr_phys,
            reserved_quantity=curr_res,
            reorder_level=curr_reorder,
            unit_cost=curr_cost,
            status=status.value if isinstance(status, InventoryStatus) else str(status),
            supplier_id=supp_id,
        )
        self._repo.record_transaction(
            sku=norm_sku,
            transaction_type="STATUS_CHANGE",
            quantity_change=0,
            quantity_before=curr_phys,
            quantity_after=curr_phys,
            reason=reason or f"Status changed to {status.value}",
            reference_type="MANUAL",
            created_by=user,
        )
        prod = catalogue_service.sku_index.get(norm_sku)
        now_str = datetime.now().isoformat()
        return InventoryItem(
            sku=norm_sku,
            physical_stock=curr_phys,
            reserved_stock=curr_res,
            reorder_level=curr_reorder,
            unit_cost=curr_cost,
            status=status,
            supplier_id=supp_id,
            source="SUPABASE_PERSISTED",
            last_updated=now_str,
            name=prod.get("name") if prod else None,
            category=prod.get("category") if prod else None,
        )

    def update_reorder_level(
        self,
        sku: str,
        reorder_level: int,
        reason: Optional[str] = None,
        user: str = "owner",
    ) -> InventoryItem:
        norm_sku = sku.strip().upper()
        item = self.get_inventory(norm_sku)
        curr_phys = item.physical_stock if item else None
        curr_res = item.reserved_stock if item else 0
        curr_cost = item.unit_cost if item else None
        curr_status = item.status if item else InventoryStatus.UNKNOWN
        supp_id = item.supplier_id if item else None

        new_status = self._compute_status(curr_phys, curr_res, reorder_level, curr_status.value)
        self._repo.upsert_inventory_item(
            sku=norm_sku,
            physical_quantity=curr_phys,
            reserved_quantity=curr_res,
            reorder_level=reorder_level,
            unit_cost=curr_cost,
            status=new_status.value,
            supplier_id=supp_id,
        )
        self._repo.record_transaction(
            sku=norm_sku,
            transaction_type="REORDER_LEVEL_CHANGE",
            quantity_change=0,
            quantity_before=curr_phys,
            quantity_after=curr_phys,
            reason=reason or f"Reorder level set to {reorder_level}",
            reference_type="MANUAL",
            created_by=user,
        )
        prod = catalogue_service.sku_index.get(norm_sku)
        now_str = datetime.now().isoformat()
        return InventoryItem(
            sku=norm_sku,
            physical_stock=curr_phys,
            reserved_stock=curr_res,
            reorder_level=reorder_level,
            unit_cost=curr_cost,
            status=new_status,
            supplier_id=supp_id,
            source="SUPABASE_PERSISTED",
            last_updated=now_str,
            name=prod.get("name") if prod else None,
            category=prod.get("category") if prod else None,
        )

    def get_transactions(
        self,
        sku: Optional[str] = None,
        limit: int = 50,
    ) -> List[InventoryTransaction]:
        rows = self._repo.get_transactions(sku=sku, limit=limit)
        txs = []
        for r in rows:
            txs.append(InventoryTransaction(
                id=str(r.get("id", "")),
                sku=r.get("sku", ""),
                transaction_type=r.get("transaction_type", ""),
                quantity_change=r.get("quantity_change", 0),
                quantity_before=r.get("quantity_before"),
                quantity_after=r.get("quantity_after"),
                reason=r.get("reason"),
                reference_type=r.get("reference_type"),
                reference_id=r.get("reference_id"),
                notes=r.get("notes"),
                created_by=r.get("created_by", "owner"),
                created_at=r.get("created_at"),
            ))
        return txs
