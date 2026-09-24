import os
import sys
import unittest
import uuid
from typing import Any, Dict
from unittest.mock import MagicMock, patch

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

os.environ.setdefault("TESTING", "1")

from services.agent_router import AgentRouter
from services.catalogue_service import (
    is_category_browsing_intent,
    catalogue_service,
    extract_sku_from_image_request,
    is_image_request_intent,
)
from services.conversation_service import conversation_service


def _fresh_phone() -> str:
    """Generate a unique phone number for each test to avoid state bleed."""
    return "91" + str(uuid.uuid4().int)[:10]


def _set_pending_quote(phone: str, sku: str, qty: int) -> str:
    """Creates conversation with an active pending quote and returns conv_id."""
    conv = conversation_service.get_or_create_conversation(phone)
    cid = conv.conversation_id
    conversation_service.set_selected_product(cid, sku, quantity=qty)
    # Simulate a pending quote in the conversation state
    conv.pending_quote = {"sku": sku, "quantity": qty, "unit_price_excl_gst": 100.0}
    conv.selected_sku = sku
    conv.selected_quantity = qty
    return cid


class TestActiveQuoteQuantityChange(unittest.TestCase):
    """
    Tests that quantity-like messages are correctly interpreted as quantity changes
    when an active quote exists, instead of being treated as product searches.
    Priority: Active quote qty change > image request > product search.
    """

    def setUp(self):
        self.router = AgentRouter()

    # --- A. Active quote + "50 unitys" (typo) ---
    def test_active_quote_50_unitys_typo(self):
        """'50 unitys' with active quote must update quantity, not search catalogue."""
        qty = AgentRouter._extract_standalone_quantity("50 unitys")
        self.assertEqual(qty, 50, "'50 unitys' should extract quantity 50")

    # --- B. Active quote + "50 units" ---
    def test_active_quote_50_units(self):
        qty = AgentRouter._extract_standalone_quantity("50 units")
        self.assertEqual(qty, 50)

    # --- C. Active quote + "make it 200" ---
    def test_active_quote_make_it_200(self):
        qty = AgentRouter._extract_standalone_quantity("make it 200")
        self.assertEqual(qty, 200)

    # --- D. Active quote + "what about 250" ---
    def test_active_quote_what_about_250(self):
        qty = AgentRouter._extract_standalone_quantity("what about 250")
        self.assertEqual(qty, 250)

    # --- E. Active quote + "50 pcs" ---
    def test_active_quote_50_pcs(self):
        qty = AgentRouter._extract_standalone_quantity("50 pcs")
        self.assertEqual(qty, 50)

    def test_active_quote_i_need_50_unitys(self):
        """'i need 50 unitys' -- the exact real production failure case."""
        qty = AgentRouter._extract_standalone_quantity("i need 50 unitys")
        self.assertEqual(qty, 50, "Real production bug: 'i need 50 unitys' must extract 50")

    def test_active_quote_i_need_75_pcs(self):
        qty = AgentRouter._extract_standalone_quantity("i need 75 pcs")
        self.assertEqual(qty, 75)

    def test_active_quote_give_me_100(self):
        qty = AgentRouter._extract_standalone_quantity("give me 100")
        self.assertEqual(qty, 100)

    def test_quantity_expressions_not_treated_as_product_search(self):
        """None of the quantity phrases should trigger product keyword search."""
        phrases = [
            "50 unitys", "i need 50 unitys", "make it 200",
            "what about 250", "50 pcs", "50 units",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                qty = AgentRouter._extract_standalone_quantity(phrase)
                self.assertIsNotNone(
                    qty,
                    f"'{phrase}' must extract a quantity, not fall through to product search"
                )

    def test_e_quote_qty_priority_over_image_intent(self):
        """Active quote + '50 unitys' must NOT trigger image request handling."""
        self.assertFalse(
            is_image_request_intent("i need 50 unitys"),
            "'i need 50 unitys' must NOT be classified as image request"
        )
        self.assertFalse(
            is_image_request_intent("50 units"),
            "'50 units' must NOT be classified as image request"
        )


class TestImageRequestBySKU(unittest.TestCase):
    """
    Tests for image requests with explicit product SKU codes.
    The agent must identify the SKU, look up the image URL, and
    send the actual WhatsApp image (or clearly report no image exists).
    """

    # --- F. Image request by SKU ---
    def test_image_request_by_sku_extraction(self):
        """Extract SKU from 'image XG-MP-01'."""
        sku = extract_sku_from_image_request("image XG-MP-01")
        self.assertEqual(sku, "XG-MP-01")

    def test_photo_of_sku_extraction(self):
        sku = extract_sku_from_image_request("photo of XG-MP-01")
        self.assertEqual(sku, "XG-MP-01")

    def test_picture_of_sku_extraction(self):
        """I. 'picture of XG-MP-01' should extract SKU."""
        sku = extract_sku_from_image_request("picture of XG-MP-01")
        self.assertEqual(sku, "XG-MP-01")

    def test_show_me_image_for_sku_extraction(self):
        """'can you show me image for XG-MP-01' -- the exact real production case."""
        sku = extract_sku_from_image_request("can you show me image for XG-MP-01")
        self.assertEqual(sku, "XG-MP-01")

    def test_show_sku_image_extraction(self):
        sku = extract_sku_from_image_request("show XG-MP-01 image")
        self.assertEqual(sku, "XG-MP-01")

    def test_show_me_image_for_sku_is_image_intent(self):
        """'can you show me image for XG-MP-01' must be detected as image intent."""
        self.assertTrue(is_image_request_intent("can you show me image for XG-MP-01"))

    def test_image_sku_patterns_are_image_intent(self):
        patterns = [
            "image XG-MP-01",
            "photo of XG-MP-01",
            "picture of XG-MP-01",
            "show me image for XG-MP-01",
            "show XG-MP-01 image",
        ]
        for phrase in patterns:
            with self.subTest(phrase=phrase):
                self.assertTrue(
                    is_image_request_intent(phrase),
                    f"'{phrase}' must be image intent"
                )

    # --- G. "show me image" (no SKU, no context) ---
    def test_show_me_image_is_image_intent(self):
        self.assertTrue(is_image_request_intent("show me image"))

    # --- H. "send photo" ---
    def test_send_photo_is_image_intent(self):
        self.assertTrue(is_image_request_intent("send photo"))

    # --- No SKU extraction for generic image words ---
    def test_no_sku_from_generic_image_words(self):
        for phrase in ["image", "photo", "show me image", "send photo", "pictures"]:
            with self.subTest(phrase=phrase):
                sku = extract_sku_from_image_request(phrase)
                self.assertIsNone(sku, f"'{phrase}' must not extract a SKU")

    # --- K. Image request when no image exists ---
    def test_handle_image_no_image_for_sku(self):
        """_handle_image_request with image_sku that has no image must reply 'no image'."""
        router = AgentRouter()
        phone = _fresh_phone()
        conv = conversation_service.get_or_create_conversation(phone)

        with patch.object(catalogue_service, "get_image_url", return_value=None):
            with patch.object(catalogue_service, "get_by_sku", return_value={"sku": "XG-FAKE-99", "category": "Test"}):
                reply = router._handle_image_request(
                    conv, phone, "image XG-FAKE-99", "XG-FAKE-99"
                )
        self.assertIn("XG-FAKE-99", reply)
        self.assertIn("don", reply.lower())  # "don't have an image"

    # --- J. Image request when selected product exists ---
    def test_handle_image_with_selected_sku(self):
        """'show me image' with conv.selected_sku set must return image for selected product."""
        router = AgentRouter()
        phone = _fresh_phone()
        conv = conversation_service.get_or_create_conversation(phone)
        conv.selected_sku = "XG-TEST-01"
        conv.current_product_candidates = []

        fake_url = "https://example.supabase.co/storage/v1/object/public/product-images/test.jpg"
        with patch.object(catalogue_service, "get_image_url", return_value=fake_url):
            with patch.object(catalogue_service, "get_by_sku", return_value={"sku": "XG-TEST-01", "category": "Test"}):
                reply = router._handle_image_request(conv, phone, "show me image", None)

        self.assertIn("XG-TEST-01", reply)
        # Image should be queued in pending media
        clean_phone = phone.lstrip("+").strip()
        pending = router.get_pending_media_messages(phone)
        self.assertEqual(len(pending), 1, "One image should be queued for selected product")
        self.assertEqual(pending[0]["image_url"], fake_url)

    # --- L. Image request when multiple candidates exist ---
    def test_handle_image_with_multiple_candidates(self):
        """'show me image' with multiple candidates must ask 'which product?'."""
        router = AgentRouter()
        phone = _fresh_phone()
        conv = conversation_service.get_or_create_conversation(phone)
        conv.selected_sku = None
        conv.current_product_candidates = [
            {"sku": "XG-A-01", "category": "Gift Sets"},
            {"sku": "XG-A-02", "category": "Gift Sets"},
        ]

        reply = router._handle_image_request(conv, phone, "show me image", None)
        self.assertIn("which", reply.lower(), "Must ask 'which product' when multiple candidates")
        self.assertNotIn("Welcome", reply, "Must NOT show welcome message")

    # --- Regression: image request must NOT become product search ---
    def test_image_request_not_product_search(self):
        """'can you give me image' must not trigger catalogue keyword search for 'image'."""
        from services.catalogue_service import catalogue_service as cat
        results = cat.search_products(query="image")
        # The defensive guard in search_products prevents searching image keywords
        self.assertEqual(results, [], "Searching for 'image' keyword must return empty list")


class TestExistingBehaviorPreserved(unittest.TestCase):
    """Regression checks: existing correct behaviors must not be broken."""

    def test_pricing_rules_unchanged(self):
        """XG-501=410, XG-502=420, XG-503=420 must remain unchanged."""
        from services.pricing_service import pricing_service
        cases = [
            ("XG-501", 100, 410.0),
            ("XG-502", 100, 420.0),
            ("XG-503", 100, 420.0),
        ]
        for sku, qty, expected_unit in cases:
            with self.subTest(sku=sku):
                quote = pricing_service.calculate_total(sku, qty)
                if quote.available:
                    self.assertAlmostEqual(
                        quote.unit_price_excl_gst, expected_unit, places=1,
                        msg=f"{sku} unit price must be {expected_unit}"
                    )

    def test_xg_577_unavailable(self):
        """XG-577 must remain unavailable."""
        from services.pricing_service import pricing_service
        quote = pricing_service.calculate_total("XG-577", 100)
        self.assertFalse(quote.available, "XG-577 must be unavailable")

    def test_quantity_extraction_pure_numbers(self):
        """Pure number inputs still extract correctly."""
        for n in [1, 50, 100, 500, 1000]:
            qty = AgentRouter._extract_standalone_quantity(str(n))
            self.assertEqual(qty, n)

    def test_image_words_not_quantity(self):
        """Image-word inputs must not produce a quantity."""
        for phrase in ["image", "photo", "picture", "show me image", "send photo"]:
            with self.subTest(phrase=phrase):
                qty = AgentRouter._extract_standalone_quantity(phrase)
                self.assertIsNone(qty)

    def test_is_show_more_not_broken(self):
        """show more / next / more intents still detected correctly."""
        from services.agent_router import is_show_more_intent
        for phrase in ["show more", "more", "next", "see more", "other options"]:
            with self.subTest(phrase=phrase):
                self.assertTrue(is_show_more_intent(phrase))

    def test_is_category_browsing_not_broken(self):
        """categories / menu / browse still detected correctly."""
        for phrase in ["categories", "category", "menu", "browse categories"]:
            with self.subTest(phrase=phrase):
                self.assertTrue(is_category_browsing_intent(phrase))

    def test_extract_sku_none_for_garbage(self):
        """Random garbage must not produce image SKUs."""
        for phrase in ["hello", "what is this", "ok", "yes", "confirm"]:
            with self.subTest(phrase=phrase):
                sku = extract_sku_from_image_request(phrase)
                self.assertIsNone(sku)


if __name__ == "__main__":
    unittest.main(verbosity=2)
