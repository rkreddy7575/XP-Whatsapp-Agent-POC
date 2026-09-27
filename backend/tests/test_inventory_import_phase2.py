import csv
import io
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import app
from services.catalogue_service import catalogue_service
from services.inventory_provider import (
    InventoryItem,
    InventoryStatus,
    SqliteInventoryProvider,
    StockAction,
)
from services.inventory_service import InventoryService


class TestInventoryImportPhase2(unittest.TestCase):
    """
    Test suite for Inventory Import Phase 2:
    - Export-to-import round trip
    - All supported headers
    - SKU normalization
    - Duplicate SKUs
    - Missing and invalid quantities
    - Negative quantities
    - Blank quantities (unknown, not zero)
    - Reorder levels
    - Add and replace modes
    - Retry safety
    - Partial batch failures
    - Transaction history
    - UNKNOWN WhatsApp messaging
    - Backend replace-mode confirmation
    - Template generation and upload
    """

    def setUp(self):
        self.provider = SqliteInventoryProvider(db_path=":memory:")
        self.service = InventoryService(provider=self.provider)
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {os.getenv('DASHBOARD_API_KEY', 'test-dashboard-secret-token-key-12345')}"}

    def test_01_export_to_import_round_trip(self):
        """Export CSV output can be immediately fed back into preview_import with 100% validity."""
        exported_csv = self.service.export_csv()
        reader = csv.DictReader(io.StringIO(exported_csv))
        rows = list(reader)

        self.assertEqual(len(rows), 768)
        preview = self.service.preview_import(rows)

        self.assertEqual(preview["total_rows"], 768)
        self.assertEqual(preview["valid_rows"], 768)
        self.assertEqual(preview["invalid_rows"], 0)
        self.assertEqual(preview["duplicate_skus_count"], 0)

    def test_02_all_supported_headers(self):
        """Supports various alias formats: export headers, raw lowercase, mixed casing."""
        rows = [
            {"SKU / Product Code": "XG-501", "Physical Quantity": "100", "Reorder Level": "50", "Imported Cost": "12.50"},
            {"sku": "XG-502", "qty": "200", "reorder": "40", "cost": "25.00"},
            {"Product Code": "XG-503", "Stock": "300", "Min Stock": "60", "Price": "30.00"},
            {"Product Name": "Keychain", "item_code": "XG-504", "physical_stock": "50", "threshold": "20", "unit_cost": "15"},
        ]
        preview = self.service.preview_import(rows)
        self.assertEqual(preview["valid_rows"], 4)
        self.assertEqual(preview["invalid_rows"], 0)

        r1 = preview["rows"][0]
        self.assertEqual(r1["sku"], "XG-501")
        self.assertEqual(r1["import_quantity"], 100)
        self.assertEqual(r1["import_reorder_level"], 50)
        self.assertEqual(r1["imported_unit_cost"], 12.50)

    def test_03_sku_normalization(self):
        """Normalizes SKUs with whitespace, hyphens, and lowercase using catalogue_service.get_by_sku()."""
        rows = [
            {"sku": "xg 502", "quantity": 10},
            {"sku": "xg-503", "quantity": 20},
            {"sku": " XG-504 ", "quantity": 30},
        ]
        preview = self.service.preview_import(rows)
        self.assertEqual(preview["valid_rows"], 3)
        self.assertEqual(preview["rows"][0]["sku"], "XG-502")
        self.assertEqual(preview["rows"][1]["sku"], "XG-503")
        self.assertEqual(preview["rows"][2]["sku"], "XG-504")

    def test_04_duplicate_skus(self):
        """Detects duplicates in import file even across different SKU format representations."""
        rows = [
            {"sku": "XG-502", "quantity": 10},
            {"sku": "xg 502", "quantity": 20},
        ]
        preview = self.service.preview_import(rows)
        self.assertEqual(preview["valid_rows"], 1)
        self.assertEqual(preview["invalid_rows"], 1)
        self.assertEqual(preview["duplicate_skus_count"], 1)
        self.assertIn("Duplicate SKU in import file", preview["rows"][1]["errors"])

    def test_05_missing_and_invalid_quantities(self):
        """Rejects non-numeric quantities, missing SKUs, and unknown SKUs."""
        rows = [
            {"sku": "XG-501", "quantity": "invalid_qty"},
            {"sku": "XG-502", "quantity": "--"},
            {"sku": "", "quantity": 50},
            {"sku": "UNKNOWN_SKU_NONEXISTENT", "quantity": 50},
        ]
        preview = self.service.preview_import(rows)
        self.assertEqual(preview["valid_rows"], 0)
        self.assertEqual(preview["invalid_rows"], 4)
        self.assertIn("Invalid numeric quantity", preview["rows"][0]["errors"])
        self.assertIn("Invalid numeric quantity", preview["rows"][1]["errors"])
        self.assertIn("Missing SKU / Product Code", preview["rows"][2]["errors"])
        self.assertIn("not found in catalogue", preview["rows"][3]["errors"][0])

    def test_06_negative_quantities(self):
        """Rejects negative physical quantities."""
        rows = [
            {"sku": "XG-501", "quantity": "-10"},
            {"sku": "XG-502", "quantity": -5},
        ]
        preview = self.service.preview_import(rows)
        self.assertEqual(preview["invalid_rows"], 2)
        self.assertIn("Quantity cannot be negative", preview["rows"][0]["errors"])
        self.assertIn("Quantity cannot be negative", preview["rows"][1]["errors"])

    def test_07_blank_quantities_mean_unknown(self):
        """Blank physical quantity is valid and represents UNKNOWN, not zero."""
        rows = [
            {"sku": "XG-501", "quantity": ""},
            {"sku": "XG-502", "quantity": "UNKNOWN"},
            {"sku": "XG-503", "quantity": 0},
        ]
        preview = self.service.preview_import(rows)
        self.assertEqual(preview["valid_rows"], 3)
        self.assertIsNone(preview["rows"][0]["import_quantity"])
        self.assertIsNone(preview["rows"][1]["import_quantity"])
        self.assertEqual(preview["rows"][2]["import_quantity"], 0)

        # Apply in replace mode
        res = self.service.apply_import(rows, mode="replace", confirm_replace=True)
        self.assertEqual(res["applied_count"], 3)

        item1 = self.service.get_inventory("XG-501")
        self.assertIsNone(item1.physical_stock)
        self.assertEqual(item1.status, InventoryStatus.UNKNOWN)

        item3 = self.service.get_inventory("XG-503")
        self.assertEqual(item3.physical_stock, 0)
        self.assertEqual(item3.status, InventoryStatus.OUT_OF_STOCK)

    def test_08_reorder_level_imports_and_validation(self):
        """Imports valid reorder levels and rejects negative or non-numeric values."""
        rows = [
            {"sku": "XG-501", "quantity": 50, "reorder_level": 75},
            {"sku": "XG-502", "quantity": 50, "reorder_level": -10},
            {"sku": "XG-503", "quantity": 50, "reorder_level": "xyz"},
        ]
        preview = self.service.preview_import(rows)
        self.assertEqual(preview["valid_rows"], 1)
        self.assertEqual(preview["invalid_rows"], 2)
        self.assertIn("Reorder level cannot be negative", preview["rows"][1]["errors"])
        self.assertIn("Invalid numeric reorder level", preview["rows"][2]["errors"])

        self.service.apply_import([rows[0]], mode="add")
        item = self.service.get_inventory("XG-501")
        self.assertEqual(item.reorder_level, 75)

    def test_09_add_and_replace_modes(self):
        """Validates add mode (addition/preservation) vs replace mode (overwrite)."""
        self.service.adjust_stock(sku="XG-501", action=StockAction.SET, quantity=50)

        # ADD mode with 30 -> 80
        self.service.apply_import([{"sku": "XG-501", "quantity": 30}], mode="add")
        self.assertEqual(self.service.get_inventory("XG-501").physical_stock, 80)

        # ADD mode with blank quantity -> remains 80
        self.service.apply_import([{"sku": "XG-501", "quantity": ""}], mode="add")
        self.assertEqual(self.service.get_inventory("XG-501").physical_stock, 80)

        # REPLACE mode with 120 -> 120
        self.service.apply_import([{"sku": "XG-501", "quantity": 120}], mode="replace", confirm_replace=True)
        self.assertEqual(self.service.get_inventory("XG-501").physical_stock, 120)

        # REPLACE mode with blank quantity -> None (UNKNOWN)
        self.service.apply_import([{"sku": "XG-501", "quantity": ""}], mode="replace", confirm_replace=True)
        self.assertIsNone(self.service.get_inventory("XG-501").physical_stock)
        self.assertEqual(self.service.get_inventory("XG-501").status, InventoryStatus.UNKNOWN)

    def test_10_retry_safety_idempotency(self):
        """Retrying an import batch with the same reference_id does not re-apply stock additions."""
        rows = [{"sku": "XG-502", "quantity": 25}]
        ref_id = "test_batch_retry_001"

        res1 = self.service.apply_import(rows, mode="add", reference_id=ref_id)
        self.assertFalse(res1["already_applied"])
        self.assertEqual(res1["applied_count"], 1)
        self.assertEqual(self.service.get_inventory("XG-502").physical_stock, 25)

        # Retry identical batch
        res2 = self.service.apply_import(rows, mode="add", reference_id=ref_id)
        self.assertTrue(res2["already_applied"])
        self.assertEqual(res2["applied_count"], 0)
        # Quantity remains 25, not 50!
        self.assertEqual(self.service.get_inventory("XG-502").physical_stock, 25)

    def test_11_partial_batch_failures(self):
        """Partial batch applies valid rows, skips invalid rows, and preserves unrelated SKUs."""
        # Pre-seed unrelated SKU
        self.service.adjust_stock(sku="XG-510", action=StockAction.SET, quantity=150)

        rows = [
            {"sku": "XG-501", "quantity": 40},
            {"sku": "NONEXISTENT_SKU", "quantity": 40},
        ]
        res = self.service.apply_import(rows, mode="add")
        self.assertEqual(res["applied_count"], 1)
        self.assertEqual(res["skipped_count"], 1)

        # XG-501 updated
        self.assertEqual(self.service.get_inventory("XG-501").physical_stock, 40)
        # Unrelated SKU XG-510 preserved
        self.assertEqual(self.service.get_inventory("XG-510").physical_stock, 150)

    def test_12_transaction_history_logging(self):
        """Verifies immutable audit trail entries created during import."""
        rows = [{"sku": "XG-501", "quantity": 60, "cost": 15.00}]
        self.service.apply_import(rows, mode="add", reference_id="imp_audit_test_99")

        txs = self.service.get_transactions("XG-501")
        self.assertGreaterEqual(len(txs), 1)
        latest_tx = txs[0]
        self.assertEqual(latest_tx.reference_type, "IMPORT")
        self.assertEqual(latest_tx.reference_id, "imp_audit_test_99")
        self.assertEqual(latest_tx.quantity_change, 60)

    def test_13_unknown_whatsapp_messaging(self):
        """Stock status for quote returns confirmation wording for UNKNOWN, not Out of Stock."""
        with patch.object(self.service, "is_live_provider", return_value=True):
            # 1. Unseeded / blank stock = UNKNOWN
            msg_unknown = self.service.get_stock_status_for_quote("XG-501", 50)
            self.assertEqual(msg_unknown, "📦 *Stock:* Stock availability is subject to confirmation.")

            # 2. In stock (100 units)
            self.service.adjust_stock(sku="XG-501", action=StockAction.SET, quantity=100)
            msg_instock = self.service.get_stock_status_for_quote("XG-501", 50)
            self.assertEqual(msg_instock, "📦 *Stock:* Available (50 units in stock)")

            # 3. Limited stock (20 available, 50 requested)
            self.service.adjust_stock(sku="XG-501", action=StockAction.SET, quantity=20)
            msg_limited = self.service.get_stock_status_for_quote("XG-501", 50)
            self.assertEqual(msg_limited, "📦 *Stock:* Limited Stock (20 units available, 50 requested)")

            # 4. Verified out of stock (0 units)
            self.service.adjust_stock(sku="XG-501", action=StockAction.SET, quantity=0)
            msg_oos = self.service.get_stock_status_for_quote("XG-501", 50)
            self.assertEqual(msg_oos, "📦 *Stock:* Currently Out of Stock")

    def test_14_backend_replace_mode_confirmation(self):
        """Backend rejects replace mode without explicit confirm_replace=True."""
        rows = [{"sku": "XG-501", "quantity": 10}]

        # Service level check
        with self.assertRaises(ValueError) as ctx:
            self.service.apply_import(rows, mode="replace", confirm_replace=False)
        self.assertIn("confirm_replace", str(ctx.exception))

        # API endpoint check via TestClient
        resp_fail = self.client.post(
            "/api/inventory/import/apply",
            headers=self.headers,
            json={"rows": rows, "mode": "replace", "confirm_replace": False},
        )
        self.assertEqual(resp_fail.status_code, 400)
        self.assertIn("confirm_replace", resp_fail.json()["detail"])

        resp_ok = self.client.post(
            "/api/inventory/import/apply",
            headers=self.headers,
            json={"rows": rows, "mode": "replace", "confirm_replace": True},
        )
        self.assertEqual(resp_ok.status_code, 200)

    def test_15_template_endpoint_generation_and_upload(self):
        """GET /api/inventory/template returns all 768 catalogue SKUs ready for import without changes."""
        resp = self.client.get("/api/inventory/template", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment; filename=mudhra_inventory_template_768_skus.csv", resp.headers.get("content-disposition", ""))

        csv_text = resp.text
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)

        self.assertEqual(len(rows), 768)
        # Verify physical quantities are blank
        for r in rows[:10]:
            self.assertEqual(r["Physical Quantity"], "")
            self.assertIn("SKU / Product Code", r)

        # Upload generated template directly into preview_import
        preview = self.service.preview_import(rows)
        self.assertEqual(preview["total_rows"], 768)
        self.assertEqual(preview["valid_rows"], 768)
        self.assertEqual(preview["invalid_rows"], 0)


if __name__ == "__main__":
    unittest.main()
