import os
import sys
import unittest

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.pricing_service import PriceRule, PricingService, pricing_service

class TestPricingService(unittest.TestCase):
    def setUp(self):
        # Create an isolated pricing service instance for testing
        self.pricing = PricingService()
        
        # Test Fixtures for XG-501 (Tiered pricing)
        # 20-49: ₹240
        # 50-99: ₹220
        # 100-249: ₹200
        # 250+: ₹180
        self.xg_501_rules = [
            PriceRule(sku="XG-501", quantity_from=20, quantity_to=49, base_price=240.0, gst_percentage=18.0, source="TEST_FIXTURE"),
            PriceRule(sku="XG-501", quantity_from=50, quantity_to=99, base_price=220.0, gst_percentage=18.0, source="TEST_FIXTURE"),
            PriceRule(sku="XG-501", quantity_from=100, quantity_to=249, base_price=200.0, gst_percentage=18.0, source="TEST_FIXTURE"),
            PriceRule(sku="XG-501", quantity_from=250, quantity_to=None, base_price=180.0, gst_percentage=18.0, source="TEST_FIXTURE"),
        ]
        self.pricing.load_rules(self.xg_501_rules)

    def test_gst_calculation(self):
        # 18% on ₹100 = ₹18.00
        self.assertEqual(PricingService.calculate_gst(100.0, 18.0), 18.0)
        # 12% on ₹150 = ₹18.00
        self.assertEqual(PricingService.calculate_gst(150.0, 12.0), 18.0)
        # 18% on ₹235.50 = ₹42.39
        self.assertEqual(PricingService.calculate_gst(235.50, 18.0), 42.39)
        # Zero or negative inputs
        self.assertEqual(PricingService.calculate_gst(0.0, 18.0), 0.0)
        self.assertEqual(PricingService.calculate_gst(100.0, 0.0), 0.0)

    def test_exact_quantity_bracket(self):
        # Mid-bracket test: qty 35 should fall into 20-49 tier (base_price: 240)
        rule = self.pricing.get_price_for_quantity("XG-501", 35)
        self.assertIsNotNone(rule)
        self.assertEqual(rule.base_price, 240.0)
        self.assertEqual(rule.quantity_from, 20)
        self.assertEqual(rule.quantity_to, 49)

    def test_lower_boundary(self):
        # Exactly on lower bounds: 20, 50, 100, 250
        rule_20 = self.pricing.get_price_for_quantity("XG-501", 20)
        self.assertIsNotNone(rule_20)
        self.assertEqual(rule_20.base_price, 240.0)

        rule_50 = self.pricing.get_price_for_quantity("XG-501", 50)
        self.assertIsNotNone(rule_50)
        self.assertEqual(rule_50.base_price, 220.0)

        rule_100 = self.pricing.get_price_for_quantity("XG-501", 100)
        self.assertIsNotNone(rule_100)
        self.assertEqual(rule_100.base_price, 200.0)

        rule_250 = self.pricing.get_price_for_quantity("XG-501", 250)
        self.assertIsNotNone(rule_250)
        self.assertEqual(rule_250.base_price, 180.0)

    def test_upper_boundary(self):
        # Exactly on upper bounds: 49, 99, 249
        rule_49 = self.pricing.get_price_for_quantity("XG-501", 49)
        self.assertIsNotNone(rule_49)
        self.assertEqual(rule_49.base_price, 240.0)

        rule_99 = self.pricing.get_price_for_quantity("XG-501", 99)
        self.assertIsNotNone(rule_99)
        self.assertEqual(rule_99.base_price, 220.0)

        rule_249 = self.pricing.get_price_for_quantity("XG-501", 249)
        self.assertIsNotNone(rule_249)
        self.assertEqual(rule_249.base_price, 200.0)

    def test_quantity_above_highest_bracket(self):
        # Quantities above 250 should all match the open-ended 250+ bracket (base_price: 180)
        for qty in [251, 500, 1000, 5000]:
            rule = self.pricing.get_price_for_quantity("XG-501", qty)
            self.assertIsNotNone(rule)
            self.assertEqual(rule.base_price, 180.0)
            self.assertIsNone(rule.quantity_to)

    def test_quantity_below_lowest_bracket(self):
        # Minimum bracket starts at 20. Qty 10 has no pricing rule -> returns None
        rule = self.pricing.get_price_for_quantity("XG-501", 10)
        self.assertIsNone(rule)

        quote = self.pricing.calculate_total("XG-501", 10)
        self.assertFalse(quote.available)
        self.assertIn("not configured yet", quote.message)

    def test_missing_sku(self):
        # SKU with no rules loaded
        rule = self.pricing.get_price_for_quantity("NON_EXISTENT_SKU", 100)
        self.assertIsNone(rule)

        quote = self.pricing.calculate_total("NON_EXISTENT_SKU", 100)
        self.assertFalse(quote.available)
        self.assertIn("not configured yet", quote.message)

    def test_missing_pricing_rule_in_production_singleton(self):
        # The production singleton has no rules loaded yet (unconnected to Google Sheet)
        quote = pricing_service.calculate_total("XG-501", 100)
        self.assertFalse(quote.available)
        self.assertIn("Pricing for *XG-501* is not configured yet. Please contact sales.", quote.message)

    def test_no_invented_fallback_price(self):
        # Ensure that no fallback/mock price is ever invented when rule is missing
        quote = self.pricing.calculate_total("GS-001", 50)
        self.assertFalse(quote.available)
        self.assertIsNone(quote.unit_price_excl_gst)
        self.assertIsNone(quote.total_price_incl_gst)
        self.assertIsNone(quote.unit_gst)

    def test_total_calculation(self):
        # Quantity 100 at ₹200 base price, 18% GST:
        # Unit excl: ₹200.00
        # Unit GST: ₹36.00
        # Unit incl: ₹236.00
        # Total excl: ₹20,000.00
        # Total GST: ₹3,600.00
        # Total incl: ₹23,600.00
        quote = self.pricing.calculate_total("XG-501", 100)
        self.assertTrue(quote.available)
        self.assertEqual(quote.sku, "XG-501")
        self.assertEqual(quote.quantity, 100)
        self.assertEqual(quote.unit_price_excl_gst, 200.0)
        self.assertEqual(quote.unit_gst, 36.0)
        self.assertEqual(quote.unit_price_incl_gst, 236.0)
        self.assertEqual(quote.total_price_excl_gst, 20000.0)
        self.assertEqual(quote.total_gst, 3600.0)
        self.assertEqual(quote.total_price_incl_gst, 23600.0)

    def test_format_quotation_available(self):
        quote = self.pricing.calculate_total("XG-501", 100)
        formatted = self.pricing.format_quotation(quote)
        self.assertIn("Quotation for XG-501", formatted)
        self.assertIn("100 units", formatted)
        self.assertIn("₹200.00", formatted)
        self.assertIn("₹36.00", formatted)
        self.assertIn("₹23,600.00", formatted)

    def test_format_quotation_unavailable(self):
        quote = self.pricing.calculate_total("GS-001", 100)
        formatted = self.pricing.format_quotation(quote)
        self.assertEqual(formatted, "Pricing for GS-001 is not configured yet. Please contact sales.")

    def test_extract_sku_and_quantity(self):
        # 'XG-501 100'
        res1 = PricingService.extract_sku_and_quantity("XG-501 100")
        self.assertEqual(res1, ("XG-501", 100))

        # '100 XG-501'
        res2 = PricingService.extract_sku_and_quantity("100 XG-501")
        self.assertEqual(res2, ("XG-501", 100))

        # 'xg-501 qty 50'
        res3 = PricingService.extract_sku_and_quantity("xg-501 qty 50")
        self.assertEqual(res3, ("XG-501", 50))

        # Pen SKU with space
        self.assertEqual(PricingService.extract_sku_and_quantity("XG-MP 01 100"), ("XG-MP 01", 100))
        self.assertEqual(PricingService.extract_sku_and_quantity("100 XG-MP 01"), ("XG-MP 01", 100))

        # Non-pricing messages should return None
        self.assertIsNone(PricingService.extract_sku_and_quantity("mugs"))
        self.assertIsNone(PricingService.extract_sku_and_quantity("categories"))
        self.assertIsNone(PricingService.extract_sku_and_quantity("XG-501"))
        self.assertIsNone(PricingService.extract_sku_and_quantity("100"))


class TestProductionPricingMaster(unittest.TestCase):
    """
    Tests verifying real production pricing loaded from pricing_master.json.
    """
    @classmethod
    def setUpClass(cls):
        # Create an independent service instance explicitly loading the production master
        cls.pricing = PricingService(load_master=True)

    def test_xg_gs_501_exists_and_returns_configured_price(self):
        # XG-GS-501 is a combo item configured at ₹410.00 base price with 18% GST
        quote = self.pricing.calculate_total("XG-GS-501", 100)
        self.assertTrue(quote.available)
        self.assertEqual(quote.sku, "XG-GS-501")
        self.assertEqual(quote.quantity, 100)
        self.assertEqual(quote.unit_price_excl_gst, 410.0)
        self.assertEqual(quote.gst_percentage, 18.0)
        self.assertEqual(quote.unit_gst, 73.8)
        self.assertEqual(quote.unit_price_incl_gst, 483.8)
        self.assertEqual(quote.total_price_excl_gst, 41000.0)
        self.assertEqual(quote.total_gst, 7380.0)
        self.assertEqual(quote.total_price_incl_gst, 48380.0)
        self.assertEqual(quote.pricing_version, "2025")

    def test_xg_501_returns_pricing_unavailable(self):
        # Older reference SKU XG-501 is absent from 2025 pricing master -> must return unavailable
        quote = self.pricing.calculate_total("XG-501", 100)
        self.assertFalse(quote.available)
        self.assertIn("Pricing for *XG-501* is not configured yet. Please contact sales.", quote.message)
        self.assertIsNone(quote.unit_price_excl_gst)
        self.assertIsNone(quote.total_price_incl_gst)

    def test_real_water_bottle_applies_12_percent_gst(self):
        # Water bottle XG-BT-001 is configured at ₹130.00 with 12% GST
        quote = self.pricing.calculate_total("XG-BT-001", 50)
        self.assertTrue(quote.available)
        self.assertEqual(quote.unit_price_excl_gst, 130.0)
        self.assertEqual(quote.gst_percentage, 12.0)
        self.assertEqual(quote.unit_gst, 15.6)
        self.assertEqual(quote.unit_price_incl_gst, 145.6)
        self.assertEqual(quote.total_price_excl_gst, 6500.0)
        self.assertEqual(quote.total_gst, 780.0)
        self.assertEqual(quote.total_price_incl_gst, 7280.0)
        self.assertEqual(quote.source_file, "WATER BOTTLES.xlsx")

    def test_real_mug_applies_18_percent_gst(self):
        # Mug XG-MG-001 is configured at ₹95.00 with 18% GST
        quote = self.pricing.calculate_total("XG-MG-001", 25)
        self.assertTrue(quote.available)
        self.assertEqual(quote.unit_price_excl_gst, 95.0)
        self.assertEqual(quote.gst_percentage, 18.0)
        self.assertEqual(quote.unit_gst, 17.1)
        self.assertEqual(quote.unit_price_incl_gst, 112.1)
        self.assertEqual(quote.total_price_excl_gst, 2375.0)
        self.assertEqual(quote.total_gst, 427.5)
        self.assertEqual(quote.total_price_incl_gst, 2802.5)
        self.assertEqual(quote.source_file, "MUGS.xlsx")

    def test_real_electronics_applies_18_percent_gst(self):
        # Electronics item XG-T-001 is configured at ₹160.00 with 18% GST
        quote = self.pricing.calculate_total("XG-T-001", 10)
        self.assertTrue(quote.available)
        self.assertEqual(quote.unit_price_excl_gst, 160.0)
        self.assertEqual(quote.gst_percentage, 18.0)
        self.assertEqual(quote.unit_gst, 28.8)
        self.assertEqual(quote.unit_price_incl_gst, 188.8)
        self.assertEqual(quote.total_price_excl_gst, 1600.0)
        self.assertEqual(quote.total_gst, 288.0)
        self.assertEqual(quote.total_price_incl_gst, 1888.0)
        self.assertEqual(quote.source_file, "ELECTRONICS AUG 2025.xlsx")

    def test_real_notebook_applies_18_percent_gst(self):
        # Notebook XG-NB-001 is configured at ₹165.00 with 18% GST
        quote = self.pricing.calculate_total("XG-NB-001", 20)
        self.assertTrue(quote.available)
        self.assertEqual(quote.unit_price_excl_gst, 165.0)
        self.assertEqual(quote.gst_percentage, 18.0)
        self.assertEqual(quote.unit_gst, 29.7)
        self.assertEqual(quote.unit_price_incl_gst, 194.7)
        self.assertEqual(quote.total_price_excl_gst, 3300.0)
        self.assertEqual(quote.total_gst, 594.0)
        self.assertEqual(quote.total_price_incl_gst, 3894.0)
        self.assertEqual(quote.source_file, "NOTEBOOK.xlsx")

    def test_real_combo_applies_18_percent_gst(self):
        # Combo XG-GS-267 is configured at ₹255.00 with 18% GST
        quote = self.pricing.calculate_total("XG-GS-267", 40)
        self.assertTrue(quote.available)
        self.assertEqual(quote.unit_price_excl_gst, 255.0)
        self.assertEqual(quote.gst_percentage, 18.0)
        self.assertEqual(quote.unit_gst, 45.9)
        self.assertEqual(quote.unit_price_incl_gst, 300.9)
        self.assertEqual(quote.total_price_excl_gst, 10200.0)
        self.assertEqual(quote.total_gst, 1836.0)
        self.assertEqual(quote.total_price_incl_gst, 12036.0)
        self.assertEqual(quote.source_file, "ALL combos price list .xlsx")

    def test_real_pen_tiers_and_quantity_boundaries(self):
        # XG-MP 01 has 3 configured tiers:
        # 1-299: ₹15.00
        # 300-499: ₹13.50
        # 500+: ₹12.50
        # All with 18% GST

        # Qty 100 -> Tier 1 (1-299)
        q100 = self.pricing.calculate_total("XG-MP 01", 100)
        self.assertTrue(q100.available)
        self.assertEqual(q100.unit_price_excl_gst, 15.0)
        self.assertEqual(q100.bracket_from, 1)
        self.assertEqual(q100.bracket_to, 299)

        # Boundary Qty 299 -> Tier 1 (1-299)
        q299 = self.pricing.calculate_total("XG-MP 01", 299)
        self.assertTrue(q299.available)
        self.assertEqual(q299.unit_price_excl_gst, 15.0)
        self.assertEqual(q299.bracket_from, 1)
        self.assertEqual(q299.bracket_to, 299)

        # Boundary Qty 300 -> Tier 2 (300-499)
        q300 = self.pricing.calculate_total("XG-MP 01", 300)
        self.assertTrue(q300.available)
        self.assertEqual(q300.unit_price_excl_gst, 13.50)
        self.assertEqual(q300.bracket_from, 300)
        self.assertEqual(q300.bracket_to, 499)

        # Boundary Qty 499 -> Tier 2 (300-499)
        q499 = self.pricing.calculate_total("XG-MP 01", 499)
        self.assertTrue(q499.available)
        self.assertEqual(q499.unit_price_excl_gst, 13.50)
        self.assertEqual(q499.bracket_from, 300)
        self.assertEqual(q499.bracket_to, 499)

        # Boundary Qty 500 -> Tier 3 (500+)
        q500 = self.pricing.calculate_total("XG-MP 01", 500)
        self.assertTrue(q500.available)
        self.assertEqual(q500.unit_price_excl_gst, 12.50)
        self.assertEqual(q500.bracket_from, 500)
        self.assertIsNone(q500.bracket_to)

        # Qty 1000 -> Tier 3 (500+)
        q1000 = self.pricing.calculate_total("XG-MP 01", 1000)
        self.assertTrue(q1000.available)
        self.assertEqual(q1000.unit_price_excl_gst, 12.50)
        self.assertEqual(q1000.bracket_from, 500)
        self.assertIsNone(q1000.bracket_to)

    def test_no_fallback_to_catalogue_source_prices(self):
        # Catalogue master has XG-501 with source_price = 260
        # Production pricing must NEVER fall back to this price
        quote = self.pricing.calculate_total("XG-501", 100)
        self.assertFalse(quote.available)
        self.assertNotEqual(quote.unit_price_excl_gst, 260.0)

    def test_source_provenance_preserved_in_quote_result(self):
        quote = self.pricing.calculate_total("XG-BT-001", 10)
        self.assertTrue(quote.available)
        self.assertEqual(quote.source_file, "WATER BOTTLES.xlsx")
        self.assertEqual(quote.source_sheet, "Sheet1")
        self.assertIsInstance(quote.source_row, int)
        self.assertEqual(quote.pricing_version, "2025")


if __name__ == "__main__":
    unittest.main()

