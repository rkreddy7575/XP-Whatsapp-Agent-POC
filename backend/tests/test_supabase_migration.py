"""
Supabase Migration & Data Mapping Test Suite
Covers:
1. Supabase schema/data mapping
2. Catalogue import (768 records)
3. Pricing import (861 records)
4. Quantity-tier pricing preservation
5. GST percentages
6. SKU normalization
7. Duplicate prevention
8. SQL seed generation integrity
"""

import json
import os
import unittest
from services.supabase_models import (
    ProductRecord,
    PricingRuleRecord,
    CustomerRecord,
    ConversationRecord,
    MessageRecord,
    EnquiryRecord,
    QuoteRecord,
    OrderRecord,
    OrderItemRecord,
    OrderStatusHistoryRecord,
    normalize_sku,
    clean_sku_key,
)
from services.supabase_data_loader import SupabaseDataLoader


class TestSupabaseMigration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = SupabaseDataLoader()
        cls.products = cls.loader.load_catalogue()
        cls.pricing = cls.loader.load_pricing()

    # -------------------------------------------------------------------------
    # 1. Supabase Schema / Data Mapping
    # -------------------------------------------------------------------------
    def test_schema_data_mapping_all_entities(self):
        # 1. ProductRecord
        p = ProductRecord(sku="XG-BT-001", category="Water Bottles", unit_price_excl_gst=130.0) if hasattr(ProductRecord, "unit_price_excl_gst") else ProductRecord(sku="XG-BT-001", category="Water Bottles")
        d_p = p.to_dict()
        self.assertEqual(d_p["sku"], "XG-BT-001")
        self.assertEqual(d_p["tenant_id"], "default")

        # 2. PricingRuleRecord
        r = PricingRuleRecord(sku="XG-BT-001", unit_price_excl_gst=130.0, gst_percentage=12.0)
        d_r = r.to_dict()
        self.assertEqual(d_r["sku"], "XG-BT-001")
        self.assertEqual(d_r["gst_percentage"], 12.0)
        self.assertEqual(d_r["quantity_from"], 1)
        self.assertIsNone(d_r["quantity_to"])

        # 3. CustomerRecord
        c = CustomerRecord(phone="919553364395", name="Ravikiran Reddy")
        self.assertEqual(c.to_dict()["phone"], "919553364395")

        # 4. ConversationRecord
        conv = ConversationRecord(conversation_id="CONV-919553364395", customer_phone="919553364395")
        self.assertEqual(conv.to_dict()["channel"], "WHATSAPP")

        # 5. MessageRecord
        msg = MessageRecord(conversation_id="CONV-919553364395", direction="INBOUND", message_text="Hello")
        self.assertEqual(msg.to_dict()["direction"], "INBOUND")

        # 6. EnquiryRecord
        enq = EnquiryRecord(enquiry_number="ENQ-20260920-0001", customer_phone="919553364395")
        self.assertEqual(enq.to_dict()["status"], "NEW")

        # 7. QuoteRecord
        q = QuoteRecord(
            quote_number="QUO-20260920-0001",
            customer_phone="919553364395",
            sku="XG-BT-001",
            quantity=100,
            unit_price_excl_gst=130.0,
            gst_percentage=12.0,
            unit_gst=15.6,
            unit_price_incl_gst=145.6,
            total_price_excl_gst=13000.0,
            total_gst=1560.0,
            total_price_incl_gst=14560.0,
        )
        self.assertEqual(q.to_dict()["total_price_incl_gst"], 14560.0)

        # 8. OrderRecord
        ord_rec = OrderRecord(
            order_id="ORD-20260920-0032",
            customer_phone="919553364395",
            subtotal=44500.0,
            gst_amount=8010.0,
            grand_total=52510.0,
        )
        self.assertEqual(ord_rec.to_dict()["status"], "CONFIRMED")

        # 9. OrderItemRecord
        item = OrderItemRecord(
            order_id="ORD-20260920-0032",
            sku="GS-001",
            quantity=100,
            unit_price=445.0,
            gst_rate=18.0,
            gst_amount=8010.0,
            line_total=52510.0,
        )
        self.assertEqual(item.to_dict()["line_total"], 52510.0)

        # 10. OrderStatusHistoryRecord
        hist = OrderStatusHistoryRecord(order_id="ORD-20260920-0032", new_status="CONFIRMED")
        self.assertEqual(hist.to_dict()["changed_by"], "system")

    # -------------------------------------------------------------------------
    # 2. Catalogue Import (768 Products)
    # -------------------------------------------------------------------------
    def test_catalogue_import_count_and_completeness(self):
        self.assertEqual(len(self.products), 768, "Must load exactly 768 catalogue products")
        for p in self.products:
            self.assertTrue(p.sku, "Product SKU must not be empty")
            self.assertTrue(p.category, "Product category must not be empty")
            self.assertEqual(p.status, "ACTIVE")
            self.assertEqual(p.tenant_id, "default")

    def test_catalogue_categories_distribution(self):
        categories = {p.category for p in self.products}
        expected_cats = {"Water Bottles", "Writing Instruments", "Electronics", "Gift Sets"}
        self.assertTrue(expected_cats.issubset(categories))

    # -------------------------------------------------------------------------
    # 3. Pricing Import (861 Records)
    # -------------------------------------------------------------------------
    def test_pricing_import_count_and_positive_prices(self):
        self.assertGreaterEqual(len(self.pricing), 861, "Must load at least 861 pricing records")
        for r in self.pricing:
            self.assertTrue(r.sku, "Pricing SKU must not be empty")
            self.assertGreater(r.unit_price_excl_gst, 0, f"Price must be positive for {r.sku}")
            self.assertEqual(r.pricing_version, "2025")
            self.assertGreaterEqual(r.quantity_from, 1)

    # -------------------------------------------------------------------------
    # 4. Quantity-Tier Pricing
    # -------------------------------------------------------------------------
    def test_quantity_tier_pricing_brackets(self):
        tiered = [r for r in self.pricing if r.quantity_to is not None or r.quantity_from > 1]
        self.assertEqual(len(tiered), 168, "Must preserve exactly 168 tiered pricing bracket records")

        # Verify a known tiered pen product: XG-MP 01
        pen_rules = [r for r in self.pricing if r.sku == "XG-MP 01"]
        self.assertGreater(len(pen_rules), 1, "XG-MP 01 must have multiple tiered rules")
        pen_rules.sort(key=lambda x: x.quantity_from)

        self.assertEqual(pen_rules[0].quantity_from, 1)
        self.assertEqual(pen_rules[0].quantity_to, 299)
        self.assertEqual(pen_rules[0].unit_price_excl_gst, 15.0)

        self.assertEqual(pen_rules[1].quantity_from, 300)
        self.assertEqual(pen_rules[1].quantity_to, 499)
        self.assertEqual(pen_rules[1].unit_price_excl_gst, 13.5)

    # -------------------------------------------------------------------------
    # 5. GST Percentage Preservation
    # -------------------------------------------------------------------------
    def test_gst_percentages_by_category(self):
        bottle_rules = [r for r in self.pricing if r.category == "Water Bottles"]
        for r in bottle_rules:
            self.assertEqual(r.gst_percentage, 12.0, f"Water bottles must have 12.0% GST: {r.sku}")

        other_rules = [r for r in self.pricing if r.category in ("Writing Instruments", "Electronics", "Mugs", "Notebooks", "Combos/Gift Sets")]
        for r in other_rules:
            self.assertEqual(r.gst_percentage, 18.0, f"Category {r.category} must have 18.0% GST: {r.sku}")

    # -------------------------------------------------------------------------
    # 6. SKU Normalization
    # -------------------------------------------------------------------------
    def test_sku_normalization_functions(self):
        self.assertEqual(normalize_sku("  xg-bt-001  "), "XG-BT-001")
        self.assertEqual(normalize_sku("gs - 002"), "GS - 002")
        self.assertEqual(clean_sku_key("XG-BT-001"), "XGBT001")
        self.assertEqual(clean_sku_key("GS - 002"), "GS002")

    # -------------------------------------------------------------------------
    # 7. Duplicate Prevention
    # -------------------------------------------------------------------------
    def test_duplicate_sku_prevention_in_catalogue(self):
        # Verify initial dataset has 0 duplicate SKUs
        skus = [p.sku for p in self.products]
        self.assertEqual(len(skus), len(set(skus)), "Catalogue must contain zero duplicate SKUs")

    def test_duplicate_bracket_prevention_in_pricing(self):
        brackets = [(r.sku, r.quantity_from, r.quantity_to, r.pricing_version) for r in self.pricing]
        self.assertEqual(len(brackets), len(set(brackets)), "Pricing must contain zero duplicate brackets")

    # -------------------------------------------------------------------------
    # 8. SQL Seed Migration Generation
    # -------------------------------------------------------------------------
    def test_source_price_preserves_multi_variant_text(self):
        """
        Regression test: Ensure multi-variant slash-separated source prices
        are preserved exactly as strings and not coerced to numbers.
        Covers:
          - XG-NB19 -> 150/150/165
          - XG-577  -> 640/800
          - XG-587  -> 750/620
        """
        prod_map = {p.sku: p for p in self.products}

        # 1. XG-NB19
        self.assertIn("XG-NB19", prod_map)
        self.assertEqual(prod_map["XG-NB19"].source_price, "150/150/165")

        # 2. XG-577
        self.assertIn("XG-577", prod_map)
        self.assertEqual(prod_map["XG-577"].source_price, "640/800")

        # 3. XG-587
        self.assertIn("XG-587", prod_map)
        self.assertEqual(prod_map["XG-587"].source_price, "750/620")

    def test_sql_seed_migration_content(self):
        test_sql_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "migrations", "test_seed.sql")
        )
        try:
            self.loader.generate_sql_seed(test_sql_path)
            self.assertTrue(os.path.exists(test_sql_path))
            with open(test_sql_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("BEGIN;", content)
            self.assertIn("INSERT INTO products", content)
            self.assertIn("INSERT INTO pricing_rules", content)
            self.assertIn("ON CONFLICT", content)
            self.assertIn("COMMIT;", content)
        finally:
            if os.path.exists(test_sql_path):
                os.remove(test_sql_path)


if __name__ == "__main__":
    unittest.main()
