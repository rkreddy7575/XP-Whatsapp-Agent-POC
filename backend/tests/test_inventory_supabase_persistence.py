import os
import sys
import unittest
import tempfile
from unittest.mock import MagicMock, patch

from services.inventory_provider import (
    AvailabilityResult,
    InventoryItem,
    InventoryStatus,
    InventoryTransaction,
    SqliteInventoryProvider,
    SupabaseInventoryProvider,
    StockAction,
)
from services.inventory_service import InventoryService
from services.supabase_repository import SupabaseInventoryRepository


class TestInventorySupabasePersistence(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.sqlite_provider = SqliteInventoryProvider(db_path=self.temp_db.name, tenant_id="test_tenant")
        self.service = InventoryService(provider=self.sqlite_provider, tenant_id="test_tenant")

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except OSError:
                pass

    # 1. SQLite local provider still works
    def test_01_sqlite_local_provider_still_works(self):
        item = self.sqlite_provider.adjust_stock(
            sku="SKU-SQLITE-01",
            action=StockAction.ADD,
            quantity=150,
            reason="Initial shipment",
            user="tester",
            unit_cost=25.50,
        )
        self.assertEqual(item.sku, "SKU-SQLITE-01")
        self.assertEqual(item.physical_stock, 150)
        self.assertEqual(item.available_stock, 150)
        self.assertEqual(item.status, InventoryStatus.IN_STOCK)

        # Check transactions
        txs = self.sqlite_provider.get_transactions("SKU-SQLITE-01")
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0].transaction_type, "STOCK_ADDED")
        self.assertEqual(txs[0].quantity_change, 150)
        self.assertEqual(txs[0].quantity_after, 150)

    # 2. Supabase inventory repository CRUD
    def test_02_supabase_inventory_repository_crud(self):
        mock_client = MagicMock()
        mock_client.is_configured = True
        mock_client.insert.return_value = [{"sku": "SKU-SB-01", "physical_quantity": 75, "tenant_id": "test_tenant"}]
        mock_client.select.return_value = [{"sku": "SKU-SB-01", "physical_quantity": 75, "tenant_id": "test_tenant", "status": "IN_STOCK"}]
        mock_client.delete.return_value = True

        repo = SupabaseInventoryRepository(client=mock_client, tenant_id="test_tenant")
        self.assertTrue(repo.is_configured)

        # Upsert
        res = repo.upsert_inventory_item("SKU-SB-01", physical_quantity=75, status="IN_STOCK")
        self.assertIsNotNone(res)
        mock_client.insert.assert_called_once()
        inserted_payload = mock_client.insert.call_args[0][1]
        self.assertEqual(inserted_payload["tenant_id"], "test_tenant")
        self.assertEqual(inserted_payload["sku"], "SKU-SB-01")
        self.assertEqual(inserted_payload["physical_quantity"], 75)

        # Get
        row = repo.get_inventory("SKU-SB-01")
        self.assertEqual(row["sku"], "SKU-SB-01")
        self.assertEqual(row["physical_quantity"], 75)

        # Delete
        deleted = repo.delete_inventory("SKU-SB-01")
        self.assertTrue(deleted)

    # 3. Tenant isolation
    def test_03_tenant_isolation(self):
        # Tenant A writes SKU-ISO
        provider_a = SqliteInventoryProvider(db_path=self.temp_db.name, tenant_id="tenant_A")
        provider_a.adjust_stock(sku="SKU-ISO", action=StockAction.SET, quantity=50)

        # Tenant B queries SKU-ISO -> should be None
        provider_b = SqliteInventoryProvider(db_path=self.temp_db.name, tenant_id="tenant_B")
        item_b = provider_b.get_inventory("SKU-ISO")
        self.assertIsNone(item_b)

        # Tenant A queries SKU-ISO -> should be 50
        item_a = provider_a.get_inventory("SKU-ISO")
        self.assertIsNotNone(item_a)
        self.assertEqual(item_a.physical_stock, 50)

    # 4. Same SKU allowed across two tenants
    def test_04_same_sku_allowed_across_two_tenants(self):
        prov_alpha = SqliteInventoryProvider(db_path=self.temp_db.name, tenant_id="tenant_alpha")
        prov_beta = SqliteInventoryProvider(db_path=self.temp_db.name, tenant_id="tenant_beta")

        # Alpha sets SKU-SHARED to 10
        item_alpha = prov_alpha.adjust_stock(sku="SKU-SHARED", action=StockAction.SET, quantity=10)
        self.assertEqual(item_alpha.physical_stock, 10)

        # Beta sets same SKU-SHARED to 90
        item_beta = prov_beta.adjust_stock(sku="SKU-SHARED", action=StockAction.SET, quantity=90)
        self.assertEqual(item_beta.physical_stock, 90)

        # Verify distinct stock levels preserved
        self.assertEqual(prov_alpha.get_inventory("SKU-SHARED").physical_stock, 10)
        self.assertEqual(prov_beta.get_inventory("SKU-SHARED").physical_stock, 90)

    # 5. Inventory transaction persistence
    def test_05_inventory_transaction_persistence(self):
        mock_client = MagicMock()
        mock_client.is_configured = True
        mock_client.insert.return_value = [{"id": "uuid-tx-1"}]
        mock_client.select.return_value = [{
            "id": "uuid-tx-1",
            "tenant_id": "tenant_1",
            "sku": "SKU-TX-TEST",
            "transaction_type": "STOCK_ADDED",
            "quantity_change": 40,
            "quantity_before": 10,
            "quantity_after": 50,
            "reason": "Replenishment",
            "reference_type": "PO",
            "reference_id": "PO-999",
            "notes": "Delivered on time",
            "created_by": "warehouse_manager",
            "created_at": "2026-09-25T12:00:00Z",
        }]

        repo = SupabaseInventoryRepository(client=mock_client, tenant_id="tenant_1")
        res = repo.record_transaction(
            sku="SKU-TX-TEST",
            transaction_type="STOCK_ADDED",
            quantity_change=40,
            quantity_before=10,
            quantity_after=50,
            reason="Replenishment",
            reference_type="PO",
            reference_id="PO-999",
            notes="Delivered on time",
            created_by="warehouse_manager",
        )
        self.assertIsNotNone(res)
        mock_client.insert.assert_called_once()
        payload = mock_client.insert.call_args[0][1]
        self.assertEqual(payload["sku"], "SKU-TX-TEST")
        self.assertEqual(payload["quantity_change"], 40)
        self.assertEqual(payload["created_by"], "warehouse_manager")

        # Fetch transactions
        txs = repo.get_transactions("SKU-TX-TEST")
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["reference_id"], "PO-999")

    # 6. Stock adjustment updates inventory + transaction atomically
    def test_06_stock_adjustment_updates_inventory_and_transaction(self):
        mock_client = MagicMock()
        mock_client.is_configured = True
        mock_client.select.return_value = []
        mock_client.insert.side_effect = [
            [{"sku": "SKU-ADJ-01", "physical_quantity": 25, "status": "LOW_STOCK"}],
            [{"id": "tx-1", "sku": "SKU-ADJ-01", "quantity_change": 25}],
        ]

        provider = SupabaseInventoryProvider(
            repo=SupabaseInventoryRepository(client=mock_client, tenant_id="tenant_adj"),
            tenant_id="tenant_adj",
        )
        item = provider.adjust_stock(
            sku="SKU-ADJ-01",
            action=StockAction.SET,
            quantity=25,
            reason="Physical inventory audit",
            user="auditor",
        )
        self.assertEqual(item.sku, "SKU-ADJ-01")
        self.assertEqual(item.physical_stock, 25)
        self.assertEqual(item.source, "SUPABASE_PERSISTED")
        self.assertEqual(mock_client.insert.call_count, 2)

    # 7. Bulk operations
    def test_07_bulk_operations(self):
        # Adjust 3 items
        adjustments = [
            {"sku": "SKU-BULK-1", "action": "set", "quantity": 10},
            {"sku": "SKU-BULK-2", "action": "set", "quantity": 20},
            {"sku": "SKU-BULK-3", "action": "set", "quantity": 30},
        ]
        results = self.service.bulk_adjust(adjustments, user="bulk_tester")
        self.assertEqual(len(results), 3)

        # Bulk status update
        status_updates = self.service.bulk_update_status(
            skus=["SKU-BULK-1", "SKU-BULK-2"],
            status="COMING_SOON",
            reason="Supplier delay",
            user="bulk_tester",
        )
        self.assertEqual(len(status_updates), 2)
        self.assertEqual(self.service.get_inventory("SKU-BULK-1").status, InventoryStatus.COMING_SOON)

        # Bulk reorder level update
        reorder_updates = self.service.bulk_update_reorder_level(
            skus=["SKU-BULK-3"],
            reorder_level=45,
            reason="Seasonality increase",
            user="bulk_tester",
        )
        self.assertEqual(len(reorder_updates), 1)
        self.assertEqual(self.service.get_inventory("SKU-BULK-3").reorder_level, 45)

    # 8. Import / upsert
    def test_08_import_upsert(self):
        rows = [
            {"sku": "GS-001", "quantity": 200, "price": 150.0},
            {"sku": "GS-002", "quantity": 80, "price": 95.0},
        ]
        preview = self.service.preview_import(rows)
        self.assertEqual(preview["valid_rows"], 2)
        self.assertEqual(preview["invalid_rows"], 0)

        # Apply import in REPLACE mode
        res_replace = self.service.apply_import(rows, mode="replace", user="importer")
        self.assertEqual(res_replace["applied_count"], 2)
        self.assertEqual(self.service.get_inventory("GS-001").physical_stock, 200)

        # Apply import in ADD mode
        res_add = self.service.apply_import([{"sku": "GS-001", "quantity": 50}], mode="add", user="importer")
        self.assertEqual(res_add["applied_count"], 1)
        self.assertEqual(self.service.get_inventory("GS-001").physical_stock, 250)

    # 9. UNKNOWN remains distinct from OUT_OF_STOCK
    def test_09_unknown_remains_distinct_from_out_of_stock(self):
        # 1. Unseeded item -> status UNKNOWN, physical None
        item_unknown = self.service.get_inventory("UNSEEDED-SKU-99")
        if item_unknown is None:
            avail_unknown = self.service.check_availability("UNSEEDED-SKU-99", 10)
            self.assertEqual(avail_unknown.status, InventoryStatus.UNKNOWN)
            self.assertFalse(avail_unknown.available)

        # 2. Item explicitly set to 0 -> status OUT_OF_STOCK, physical 0
        self.service.adjust_stock("OUT-OF-STOCK-SKU", action=StockAction.SET, quantity=0)
        item_oos = self.service.get_inventory("OUT-OF-STOCK-SKU")
        self.assertEqual(item_oos.physical_stock, 0)
        self.assertEqual(item_oos.status, InventoryStatus.OUT_OF_STOCK)

        # 3. Item with None physical stock
        item_created = self.service.provider.get_or_create_inventory("UNTRACKED-SKU")
        self.assertIsNone(item_created.physical_stock)
        self.assertEqual(item_created.status, InventoryStatus.UNKNOWN)

    # 10. Production provider selection
    def test_10_production_provider_selection(self):
        service = InventoryService()
        # In test suite, defaults to SQLite
        self.assertFalse(service.is_supabase_primary)
        self.assertIsInstance(service.provider, SqliteInventoryProvider)

        # With mock configured Supabase and USE_SUPABASE_IN_TESTS=1
        with patch.dict(os.environ, {"USE_SUPABASE_IN_TESTS": "1"}):
            with patch("services.supabase_repository.SupabaseClient.is_configured", True):
                sb_service = InventoryService()
                self.assertTrue(sb_service.is_supabase_primary)
                self.assertIsInstance(sb_service.provider, SupabaseInventoryProvider)

    # 11. Production does not silently fall back to SQLite
    def test_11_production_does_not_silently_fallback_to_sqlite(self):
        mock_client = MagicMock()
        mock_client.is_configured = False  # Broken / unconfigured
        repo = SupabaseInventoryRepository(client=mock_client, tenant_id="prod_tenant")
        provider = SupabaseInventoryProvider(repo=repo, tenant_id="prod_tenant")

        # When Supabase is unconfigured, attempting writes must raise RuntimeError, NOT silently succeed in SQLite
        with self.assertRaises(RuntimeError):
            provider.adjust_stock("SKU-PROD-FAIL", action=StockAction.SET, quantity=50)

    # 12. Existing WhatsApp audit tests remain green
    def test_12_existing_whatsapp_audit_interop(self):
        from services.audit_service import audit_service
        summary = audit_service.get_metrics_summary(hours=24, production_only=True)
        self.assertIn("total_received", summary)
        self.assertIn("total_sent", summary)
        self.assertIn("failure_rate_percent", summary)


if __name__ == "__main__":
    unittest.main()
