import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class InventoryStatus(str, Enum):
    IN_STOCK = "IN_STOCK"
    LOW_STOCK = "LOW_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    COMING_SOON = "COMING_SOON"
    SUPPLIER_CONFIRMATION_REQUIRED = "SUPPLIER_CONFIRMATION_REQUIRED"
    UNKNOWN = "UNKNOWN"


@dataclass
class InventoryItem:
    """Represents the inventory state of a product SKU."""
    sku: str
    physical_stock: int
    reserved_stock: int
    reorder_level: Optional[int] = None
    status: InventoryStatus = InventoryStatus.IN_STOCK
    supplier_id: Optional[str] = None
    source: str = "DEVELOPMENT_MOCK"
    last_updated: Optional[str] = None
    is_mock: bool = True

    @property
    def available_stock(self) -> int:
        """Available stock = physical_stock - reserved_stock. Never negative."""
        return max(0, self.physical_stock - self.reserved_stock)


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
    """Abstract interface for inventory providers (Development mock vs live Google Sheets)."""

    @abstractmethod
    def is_live(self) -> bool:
        """Returns True if this provider connects to a real live inventory source."""
        pass

    @abstractmethod
    def get_inventory(self, sku: str) -> Optional[InventoryItem]:
        """Fetches the inventory record for a given SKU."""
        pass

    @abstractmethod
    def check_availability(self, sku: str, requested_quantity: int) -> AvailabilityResult:
        """Checks if the requested quantity is available in stock."""
        pass

    @abstractmethod
    def get_available_quantity(self, sku: str) -> int:
        """Returns available quantity for an SKU (or 0 if not found)."""
        pass

    @abstractmethod
    def reserve_stock(self, sku: str, quantity: int) -> bool:
        """Reserves a specific quantity of stock. Returns True if successful."""
        pass

    @abstractmethod
    def release_stock(self, sku: str, quantity: int) -> bool:
        """Releases a previously reserved quantity. Returns True if successful."""
        pass


class DevelopmentInventoryProvider(InventoryProvider):
    """
    In-memory development and testing inventory provider.
    Loads initial data from backend/data/inventory_mock.json.
    Explicitly labeled as NON-LIVE development data.
    """

    def __init__(self, data_path: Optional[str] = None, initial_items: Optional[List[InventoryItem]] = None):
        self._items: Dict[str, InventoryItem] = {}
        if initial_items:
            for itm in initial_items:
                self._add_item(itm)
        else:
            if data_path is None:
                data_path = os.path.join(os.path.dirname(__file__), "..", "data", "inventory_mock.json")
            self._load_from_json(data_path)

    def is_live(self) -> bool:
        return False

    def _add_item(self, item: InventoryItem) -> None:
        # Enforce business rules at load time:
        # Physical stock cannot be negative
        if item.physical_stock < 0:
            raise ValueError(f"physical_stock cannot be negative for {item.sku}")
        # Reserved stock cannot be negative
        if item.reserved_stock < 0:
            raise ValueError(f"reserved_stock cannot be negative for {item.sku}")
        # Reserved stock cannot exceed physical stock
        if item.reserved_stock > item.physical_stock:
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

        # Cannot reserve more than available stock
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

        # Cannot release more than currently reserved
        if quantity > item.reserved_stock:
            return False

        item.reserved_stock -= quantity
        item.last_updated = datetime.now().isoformat()
        return True
