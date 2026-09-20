import os
from typing import Optional

from services.inventory_provider import (
    AvailabilityResult,
    DevelopmentInventoryProvider,
    InventoryItem,
    InventoryProvider,
    InventoryStatus,
)


class InventoryService:
    """
    Business service layer managing inventory lookups and reservations.
    Decoupled from specific inventory backends via the InventoryProvider interface.
    Ready to switch seamlessly to GoogleSheetsInventoryProvider in the future.
    """

    def __init__(self, provider: Optional[InventoryProvider] = None):
        # Default to DevelopmentInventoryProvider for local PoC
        self._provider: InventoryProvider = provider or DevelopmentInventoryProvider()

    @property
    def provider(self) -> InventoryProvider:
        return self._provider

    def set_provider(self, provider: InventoryProvider) -> None:
        """Allows swapping the inventory provider (e.g. to Google Sheets)."""
        self._provider = provider

    def is_live_provider(self) -> bool:
        """Returns True if the current provider represents live business stock."""
        return self._provider.is_live()

    def get_inventory(self, sku: str) -> Optional[InventoryItem]:
        """Fetches the inventory record for a product code."""
        return self._provider.get_inventory(sku)

    def check_availability(self, sku: str, requested_quantity: int) -> AvailabilityResult:
        """Checks if a requested quantity is currently available in stock."""
        return self._provider.check_availability(sku, requested_quantity)

    def get_available_quantity(self, sku: str) -> int:
        """Returns the available quantity (physical - reserved) for an SKU."""
        return self._provider.get_available_quantity(sku)

    def reserve_stock(self, sku: str, quantity: int) -> bool:
        """Attempts to reserve stock for an order/quote."""
        return self._provider.reserve_stock(sku, quantity)

    def release_stock(self, sku: str, quantity: int) -> bool:
        """Releases previously reserved stock."""
        return self._provider.release_stock(sku, quantity)

    def get_stock_status_for_quote(self, sku: str, requested_quantity: int) -> str:
        """
        Determines customer-facing stock messaging for a WhatsApp quotation.
        
        CRITICAL BUSINESS RULE:
        If using the development mock provider, the quote MUST NOT claim test quantities
        are real production availability. Instead, it displays:
        '📦 Stock: Availability confirmation required'
        """
        if not self.is_live_provider():
            return "📦 *Stock:* Availability confirmation required"

        # Logic when connected to live Google Sheets in the future:
        availability = self.check_availability(sku, requested_quantity)
        if availability.available:
            return f"📦 *Stock:* Available ({requested_quantity} units in stock)"
        elif availability.available_stock > 0:
            return f"📦 *Stock:* Limited Stock ({availability.available_stock} units available, {requested_quantity} requested)"
        else:
            return "📦 *Stock:* Currently Out of Stock"


# Singleton instance for application use
inventory_service = InventoryService()
