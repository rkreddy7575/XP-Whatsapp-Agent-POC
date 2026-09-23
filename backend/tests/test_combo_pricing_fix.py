import os
import sys
import unittest

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.pricing_service import pricing_service
from services.agent_router import agent_router


class TestComboPricingFix(unittest.TestCase):
    """
    Regression tests verifying that customer Combo SKUs resolve deterministically
    to their authoritative 2025 pricing in ALL combos price list .xlsx (via pricing_master.json).
    Ensures 2024 archive prices are never used and unresolved SKUs remain unavailable.
    """

    def setUp(self):
        pricing_service.load_pricing_master()

    def test_xg_501_authoritative_pricing(self):
        """Verify XG-501 resolves to current authoritative Rs. 410.0 base price (3 IN 1, row 50)."""
        rule = pricing_service.get_price_for_quantity("XG-501", 1)
        self.assertIsNotNone(rule, "XG-501 must resolve a valid pricing rule")
        self.assertEqual(rule.base_price, 410.0)
        self.assertEqual(rule.gst_percentage, 18.0)
        self.assertEqual(rule.source_file, "ALL combos price list .xlsx")
        self.assertEqual(rule.source_sheet, "3 IN 1")
        self.assertEqual(rule.source_row, 50)

        calc = pricing_service.calculate_total("XG-501", 10)
        self.assertIsNotNone(calc)
        self.assertTrue(calc.available)
        self.assertEqual(calc.unit_price_excl_gst, 410.0)
        self.assertEqual(calc.total_price_excl_gst, 4100.0)
        self.assertEqual(calc.total_gst, 738.0)
        self.assertEqual(calc.total_price_incl_gst, 4838.0)

    def test_xg_502_authoritative_pricing(self):
        """Verify XG-502 resolves to current authoritative Rs. 420.0 base price (3 IN 1, row 51)."""
        rule = pricing_service.get_price_for_quantity("XG-502", 1)
        self.assertIsNotNone(rule, "XG-502 must resolve a valid pricing rule")
        self.assertEqual(rule.base_price, 420.0)
        self.assertEqual(rule.gst_percentage, 18.0)
        self.assertEqual(rule.source_file, "ALL combos price list .xlsx")
        self.assertEqual(rule.source_sheet, "3 IN 1")
        self.assertEqual(rule.source_row, 51)

        calc = pricing_service.calculate_total("XG-502", 5)
        self.assertIsNotNone(calc)
        self.assertTrue(calc.available)
        self.assertEqual(calc.unit_price_excl_gst, 420.0)
        self.assertEqual(calc.total_price_excl_gst, 2100.0)
        self.assertAlmostEqual(calc.total_gst, 378.0, places=2)
        self.assertAlmostEqual(calc.total_price_incl_gst, 2478.0, places=2)

    def test_unresolved_skus_remain_unavailable(self):
        """
        Verify that ambiguous/mismatched SKUs without authoritative pricing in
        ALL combos price list .xlsx remain unavailable rather than inventing or falling back.
        """
        # XG-577: 6-in-1 combo in catalogue, only ambiguous/mismatched 2-in-1 in workbook
        calc_577 = pricing_service.calculate_total("XG-577", 10)
        self.assertFalse(calc_577.available)
        self.assertIn("not configured", calc_577.message.lower())

        # XG-587: 7-in-1 combo in catalogue, only ambiguous/mismatched 2-in-1 in workbook
        calc_587 = pricing_service.calculate_total("XG-587", 10)
        self.assertFalse(calc_587.available)
        self.assertIn("not configured", calc_587.message.lower())

    def test_direct_quote_fast_path_for_combos(self):
        """Verify AgentRouter direct quotation fast-path uses authoritative current pricing."""
        phone = "919876543299"
        reply = agent_router.handle_incoming_message(
            customer_phone=phone,
            message_text="XG-501 50",
            customer_name="Test Buyer",
        )
        self.assertIn("XG-501", reply)
        self.assertIn("410", reply)
        self.assertIn("Quotation", reply)


if __name__ == "__main__":
    unittest.main()
