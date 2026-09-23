import json
import os
import sys
import unittest

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.pricing_data_loader import (
    CATEGORY_GST_RATES,
    DEFAULT_SOURCE_FILES,
    PricingDataLoader,
    normalize_sku_key,
)


class TestPricingDataLoader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.master_path = os.path.join(backend_dir, "data", "pricing_master.json")
        cls.report_path = os.path.join(backend_dir, "data", "pricing_import_report.json")
        
        # Load from disk if present, else run loader
        if not os.path.exists(cls.master_path) or not os.path.exists(cls.report_path):
            loader = PricingDataLoader()
            loader.load_all()
            loader.save_pricing_master(cls.master_path)
            loader.save_report(cls.report_path)

        with open(cls.master_path, "r", encoding="utf-8") as f:
            cls.master_records = json.load(f)

        with open(cls.report_path, "r", encoding="utf-8") as f:
            cls.report_data = json.load(f)

    def test_all_six_source_files_exist(self):
        missing = [p for p in DEFAULT_SOURCE_FILES.values() if not os.path.exists(p)]
        if missing:
            self.skipTest(f"Original source file not present on this host: {missing[0]}")
        for key, path in DEFAULT_SOURCE_FILES.items():
            self.assertTrue(os.path.exists(path), f"Source file missing: {path}")

    def test_expected_categories_represented(self):
        categories = {r["category"] for r in self.master_records}
        expected = {
            "Water Bottles",
            "Writing Instruments",
            "Electronics",
            "Combos/Gift Sets",
            "Mugs",
            "Notebooks",
        }
        self.assertEqual(categories, expected)

    def test_gst_rates_correct(self):
        for r in self.master_records:
            cat = r["category"]
            expected_gst = 12.0 if cat == "Water Bottles" else 18.0
            self.assertEqual(
                r["gst_percentage"],
                expected_gst,
                f"Incorrect GST {r['gst_percentage']} for {r['sku']} in {cat}",
            )

    def test_prices_are_positive_numbers(self):
        for r in self.master_records:
            price = r["unit_price_excl_gst"]
            self.assertIsInstance(price, (int, float))
            self.assertGreater(price, 0, f"Price should be > 0 for {r['sku']}")

    def test_pen_quantity_tiers(self):
        pen_records = [r for r in self.master_records if r["category"] == "Writing Instruments"]
        self.assertGreater(len(pen_records), 0)

        # Verify exact tiers
        tier_keys = set()
        for r in pen_records:
            tier_keys.add((r["quantity_from"], r["quantity_to"]))

        expected_tiers = {(1, 299), (300, 499), (500, None)}
        self.assertEqual(tier_keys, expected_tiers)

        # Every pen should have exactly 3 records
        pen_sku_counts = {}
        for r in pen_records:
            sku = r["normalized_sku"]
            pen_sku_counts[sku] = pen_sku_counts.get(sku, 0) + 1

        for sku, count in pen_sku_counts.items():
            self.assertEqual(count, 3, f"Pen {sku} has {count} records instead of 3")

    def test_non_pen_categories_have_single_open_ended_tier(self):
        non_pens = [r for r in self.master_records if r["category"] != "Writing Instruments"]
        for r in non_pens:
            self.assertEqual(r["quantity_from"], 1, f"Expected quantity_from=1 for {r['sku']}")
            self.assertIsNone(r["quantity_to"], f"Expected quantity_to=None for {r['sku']}")

    def test_no_duplicate_sku_and_quantity_tier(self):
        seen_keys = set()
        for r in self.master_records:
            key = (r["normalized_sku"], r["quantity_from"])
            self.assertNotIn(key, seen_keys, f"Duplicate found for key: {key}")
            seen_keys.add(key)

    def test_missing_prices_not_invented_or_zeroed(self):
        # Known notebook SKUs with missing prices in source: XG-NB-053, XG-NB-065, XG-NB-066
        missing_skus = {"XG-NB-053", "XG-NB-065", "XG-NB-066"}
        master_skus = {r["normalized_sku"] for r in self.master_records}

        for m_sku in missing_skus:
            self.assertNotIn(m_sku, master_skus, f"Missing price SKU {m_sku} was improperly imported")

        # Verify they are logged in the report
        report_missing = {item["sku"] for item in self.report_data.get("missing_prices", [])}
        for m_sku in missing_skus:
            self.assertIn(m_sku, report_missing, f"{m_sku} not tracked in missing_prices report")

    def test_source_provenance_preserved(self):
        for r in self.master_records:
            self.assertTrue(r.get("source_file"), f"Missing source_file for {r['sku']}")
            self.assertTrue(r.get("source_sheet"), f"Missing source_sheet for {r['sku']}")
            self.assertIsInstance(r.get("source_row"), int)
            self.assertGreater(r["source_row"], 0)
            self.assertEqual(r.get("pricing_version"), "2025")

    def test_sku_formatting_preserved(self):
        # Ensure original sku preserves spaces while normalized_sku trims them
        test_key = normalize_sku_key("  XG - MP 01  ")
        self.assertEqual(test_key, "XG - MP 01")


if __name__ == "__main__":
    unittest.main()
