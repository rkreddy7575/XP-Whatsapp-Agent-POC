import os
import sys
import unittest

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.inventory_provider import (
    AvailabilityResult,
    DevelopmentInventoryProvider,
    InventoryItem,
    InventoryProvider,
    InventoryStatus,
)
from services.inventory_service import InventoryService, inventory_service


class TestInventoryService(unittest.TestCase):
    def setUp(self):
        # Create an isolated development provider with deterministic test data
        self.test_items = [
            InventoryItem(
                sku="TEST-ITEM-01",
                physical_stock=100,
                reserved_stock=20,
                reorder_level=15,
                status=InventoryStatus.IN_STOCK,
            ),
            InventoryItem(
                sku="TEST-ITEM-ZERO",
                physical_stock=0,
                reserved_stock=0,
                reorder_level=10,
                status=InventoryStatus.OUT_OF_STOCK,
            ),
            InventoryItem(
                sku="TEST-ITEM-RESERVED-EQUAL",
                physical_stock=50,
                reserved_stock=50,
                status=InventoryStatus.OUT_OF_STOCK,
            ),
        ]
        self.provider = DevelopmentInventoryProvider(initial_items=self.test_items)
        self.inventory = InventoryService(provider=self.provider)

    def test_1_sku_with_available_stock(self):
        # TEST-ITEM-01 has 100 physical, 20 reserved -> 80 available
        item = self.inventory.get_inventory("TEST-ITEM-01")
        self.assertIsNotNone(item)
        self.assertEqual(item.physical_stock, 100)
        self.assertEqual(item.available_stock, 80)

    def test_2_sku_with_reserved_stock(self):
        # Verify reserved stock is accurately stored and reflected
        item = self.inventory.get_inventory("TEST-ITEM-01")
        self.assertIsNotNone(item)
        self.assertEqual(item.reserved_stock, 20)

    def test_3_available_stock_calculation(self):
        # available_stock = physical_stock - reserved_stock
        item = self.inventory.get_inventory("TEST-ITEM-01")
        self.assertEqual(item.available_stock, item.physical_stock - item.reserved_stock)

        # When physical == reserved, available == 0
        item_eq = self.inventory.get_inventory("TEST-ITEM-RESERVED-EQUAL")
        self.assertEqual(item_eq.available_stock, 0)

    def test_4_exact_quantity_availability(self):
        # TEST-ITEM-01 has 80 available. Exactly 80 should be available
        res = self.inventory.check_availability("TEST-ITEM-01", 80)
        self.assertTrue(res.available)
        self.assertEqual(res.available_stock, 80)
        self.assertEqual(res.requested_quantity, 80)

    def test_5_requested_quantity_below_available_stock(self):
        # 50 requested, 80 available -> True
        res = self.inventory.check_availability("TEST-ITEM-01", 50)
        self.assertTrue(res.available)
        self.assertEqual(res.available_stock, 80)

    def test_6_requested_quantity_equal_to_available_stock(self):
        # 80 requested, 80 available -> True
        res = self.inventory.check_availability("TEST-ITEM-01", 80)
        self.assertTrue(res.available)

    def test_7_requested_quantity_above_available_stock(self):
        # 81 requested, 80 available -> False
        res = self.inventory.check_availability("TEST-ITEM-01", 81)
        self.assertFalse(res.available)
        self.assertEqual(res.available_stock, 80)

    def test_8_unknown_sku(self):
        # SKU not in inventory
        item = self.inventory.get_inventory("NON-EXISTENT-SKU")
        self.assertIsNone(item)
        self.assertEqual(self.inventory.get_available_quantity("NON-EXISTENT-SKU"), 0)

        res = self.inventory.check_availability("NON-EXISTENT-SKU", 10)
        self.assertFalse(res.available)
        self.assertEqual(res.available_stock, 0)
        self.assertEqual(res.status, InventoryStatus.UNKNOWN)

    def test_9_zero_stock(self):
        # Item with 0 physical and 0 reserved
        res = self.inventory.check_availability("TEST-ITEM-ZERO", 1)
        self.assertFalse(res.available)
        self.assertEqual(res.available_stock, 0)

    def test_10_reserved_stock_cannot_exceed_physical_stock(self):
        # Attempting to construct an InventoryItem where reserved > physical must raise ValueError
        with self.assertRaises(ValueError):
            DevelopmentInventoryProvider(initial_items=[
                InventoryItem(sku="INVALID", physical_stock=50, reserved_stock=51)
            ])

    def test_11_reservation_reduces_available_stock(self):
        # TEST-ITEM-01 starts with 80 available. Reserve 30 -> 50 available
        self.assertEqual(self.inventory.get_available_quantity("TEST-ITEM-01"), 80)
        success = self.inventory.reserve_stock("TEST-ITEM-01", 30)
        self.assertTrue(success)
        self.assertEqual(self.inventory.get_available_quantity("TEST-ITEM-01"), 50)
        item = self.inventory.get_inventory("TEST-ITEM-01")
        self.assertEqual(item.reserved_stock, 50)

    def test_12_releasing_reservation_increases_available_stock(self):
        # TEST-ITEM-01 has 20 reserved, 80 available. Release 10 -> 10 reserved, 90 available
        success = self.inventory.release_stock("TEST-ITEM-01", 10)
        self.assertTrue(success)
        self.assertEqual(self.inventory.get_available_quantity("TEST-ITEM-01"), 90)
        item = self.inventory.get_inventory("TEST-ITEM-01")
        self.assertEqual(item.reserved_stock, 10)

    def test_13_cannot_reserve_more_than_available_stock(self):
        # TEST-ITEM-01 has 80 available. Reserving 85 must fail and not change stock
        success = self.inventory.reserve_stock("TEST-ITEM-01", 85)
        self.assertFalse(success)
        self.assertEqual(self.inventory.get_available_quantity("TEST-ITEM-01"), 80)

        # Negative or zero reservation
        self.assertFalse(self.inventory.reserve_stock("TEST-ITEM-01", 0))
        self.assertFalse(self.inventory.reserve_stock("TEST-ITEM-01", -10))

    def test_14_cannot_create_negative_stock(self):
        # Negative physical stock at init raises ValueError
        with self.assertRaises(ValueError):
            DevelopmentInventoryProvider(initial_items=[
                InventoryItem(sku="NEG-PHYS", physical_stock=-10, reserved_stock=0)
            ])

        # Negative reserved stock at init raises ValueError
        with self.assertRaises(ValueError):
            DevelopmentInventoryProvider(initial_items=[
                InventoryItem(sku="NEG-RES", physical_stock=10, reserved_stock=-5)
            ])

        # Releasing more than reserved must fail and not create negative reserved stock
        success = self.inventory.release_stock("TEST-ITEM-01", 25)  # only 20 reserved
        self.assertFalse(success)
        item = self.inventory.get_inventory("TEST-ITEM-01")
        self.assertEqual(item.reserved_stock, 20)

    def test_15_development_provider_disclaimer_in_quote_status(self):
        # Since development mock provider is not live, get_stock_status_for_quote MUST return None (no fake stock claims)
        status_msg = self.inventory.get_stock_status_for_quote("TEST-ITEM-01", 10)
        self.assertIsNone(status_msg)
        self.assertFalse(self.inventory.is_live_provider())

        # When a live inventory provider is connected, get_stock_status_for_quote returns dynamic availability
        class MockLiveProvider(DevelopmentInventoryProvider):
            def is_live(self) -> bool:
                return True

        live_service = InventoryService(
            provider=MockLiveProvider(
                initial_items=[
                    InventoryItem(sku="LIVE-01", physical_stock=100, reserved_stock=10)
                ]
            )
        )
        self.assertTrue(live_service.is_live_provider())
        live_status = live_service.get_stock_status_for_quote("LIVE-01", 50)
        self.assertIsNotNone(live_status)
        self.assertIn("Available", live_status)

    def test_16_mock_file_loads_correctly_in_default_singleton(self):
        # The application default singleton loads from backend/data/inventory_mock.json
        # Check that representative test SKUs are loaded
        item_gs = inventory_service.get_inventory("XG-GS-501")
        self.assertIsNotNone(item_gs)
        self.assertEqual(item_gs.physical_stock, 1000)
        self.assertEqual(item_gs.reserved_stock, 100)
        self.assertEqual(item_gs.available_stock, 900)

        item_bt = inventory_service.get_inventory("XG-BT-001")
        self.assertIsNotNone(item_bt)
        self.assertEqual(item_bt.physical_stock, 500)
        self.assertEqual(item_bt.reserved_stock, 50)
        self.assertEqual(item_bt.available_stock, 450)

        item_mg = inventory_service.get_inventory("XG-MG-001")
        self.assertIsNotNone(item_mg)
        self.assertEqual(item_mg.physical_stock, 200)
        self.assertEqual(item_mg.reserved_stock, 25)
        self.assertEqual(item_mg.available_stock, 175)


if __name__ == "__main__":
    unittest.main()
