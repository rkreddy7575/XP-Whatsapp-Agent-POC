import uuid
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

# Ensure backend root is on sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi.testclient import TestClient
from main import app
from services.agent_router import agent_router
from services.catalogue_service import catalogue_service, is_category_browsing_intent
from services.conversation_service import conversation_service
from services.pricing_service import pricing_service


def make_webhook_payload(body_text: str, sender: str = "919876543210", name: str = "Test Buyer"):
    """Helper to generate a Meta Cloud API incoming message webhook payload."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "2096262090982740",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15551868203",
                                "phone_number_id": "1343824278810259",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": name},
                                    "wa_id": sender,
                                }
                            ],
                            "messages": [
                                {
                                    "from": sender,
                                    "id": f"wamid.test_{uuid.uuid4().hex[:12]}",
                                    "timestamp": "1726750000",
                                    "text": {"body": body_text},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


class TestCategoryBrowsingFlow(unittest.TestCase):
    """
    Regression test suite for WhatsApp category browsing and selection.
    Verifies:
    - 'Categories' returns clean numbered list without invoking Gemini or searching products.
    - Phrasing variations ('category', 'show categories', '*Categories*') trigger category browsing.
    - Category number selection ('1', '3', '10') returns products for the selected category.
    - Category name selection ('Gift Sets', 'combos', 'mugs') returns products for the category.
    - Multi-turn flow preserves state: Categories -> number -> product index -> quote -> confirm.
    - 'Categories' never reaches product keyword search.
    """

    def setUp(self):
        self.client = TestClient(app)
        self.sender = "919876543210"
        # Reset conversation state
        conv = conversation_service.get_or_create_conversation(self.sender)
        conversation_service.clear_selection(conv.conversation_id)
        conversation_service.set_candidates(conv.conversation_id, [])

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_1_categories_command_returns_clean_numbered_list(self, mock_send, mock_gemini):
        """'Categories' returns dynamic numbered list of catalogue categories without Gemini."""
        payload = make_webhook_payload("Categories", sender=self.sender)
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)

        mock_send.assert_called_once()
        msg = mock_send.call_args[1].get("message") or mock_send.call_args[0][1]

        # Must NOT call Gemini
        mock_gemini.assert_not_called()

        # Must NOT be a product search failure
        self.assertNotIn("No products found matching", msg)
        self.assertNotIn("*Categories*", msg.split("\n")[0])  # No error header

        # Must display clean WhatsApp-friendly category menu
        self.assertIn("Mudhra Branding Solutions", msg)
        self.assertIn("Product Categories", msg)

        # Dynamic categories from catalogue
        categories = catalogue_service.get_categories()
        self.assertGreaterEqual(len(categories), 5)
        for idx, cat in enumerate(categories, 1):
            self.assertIn(f"{idx}.", msg)
            self.assertIn(cat["category"], msg)
            self.assertIn(f"{cat['count']} items", msg)

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_2_phrasing_variations_trigger_category_menu(self, mock_send, mock_gemini):
        """Phrasing variations all trigger the category menu deterministically."""
        variations = [
            "category",
            "categories",
            "Categories",
            "*Categories*",
            "*Categories",
            "show categories",
            "browse categories",
            "show me categories",
            "what categories do you have",
            "what categories do you have?",
            "catalogue",
        ]

        for phrase in variations:
            mock_send.reset_mock()
            mock_gemini.reset_mock()

            payload = make_webhook_payload(phrase, sender=self.sender)
            response = self.client.post("/webhook", json=payload)
            self.assertEqual(response.status_code, 200)

            mock_send.assert_called_once()
            msg = mock_send.call_args[1].get("message") or mock_send.call_args[0][1]

            mock_gemini.assert_not_called()
            self.assertNotIn("No products found matching", msg, f"Failed on variation: {phrase}")
            self.assertIn("Product Categories", msg, f"Failed on variation: {phrase}")
            self.assertIn("Gift Sets", msg, f"Failed on variation: {phrase}")

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_3_category_number_selection(self, mock_send, mock_gemini):
        """Replying with a number after 'Categories' selects that specific category."""
        # 1. Ask for categories
        payload = make_webhook_payload("Categories", sender=self.sender)
        self.client.post("/webhook", json=payload)

        # 2. Select category 3 (Gift Sets)
        mock_send.reset_mock()
        mock_gemini.reset_mock()

        payload = make_webhook_payload("3", sender=self.sender)
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)

        mock_send.assert_called_once()
        msg = mock_send.call_args[1].get("message") or mock_send.call_args[0][1]

        mock_gemini.assert_not_called()
        self.assertIn("Found", msg)
        self.assertIn("Gift Sets", msg)
        self.assertIn("SKU:", msg)
        self.assertIn("GS-001", msg)

        # 3. Select category 10 (Writing Instruments)
        # Ask for categories again
        self.client.post("/webhook", json=make_webhook_payload("Categories", sender=self.sender))
        mock_send.reset_mock()

        payload = make_webhook_payload("10", sender=self.sender)
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)

        mock_send.assert_called_once()
        msg = mock_send.call_args[1].get("message") or mock_send.call_args[0][1]
        self.assertIn("Found", msg)
        self.assertIn("Writing Instruments", msg)

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_4_category_name_selection(self, mock_send, mock_gemini):
        """Replying with category name (e.g. 'Gift Sets', 'combos') returns products."""
        # 1. Direct category name: Gift Sets
        payload = make_webhook_payload("Gift Sets", sender=self.sender)
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)

        mock_send.assert_called_once()
        msg = mock_send.call_args[1].get("message") or mock_send.call_args[0][1]
        mock_gemini.assert_not_called()
        self.assertIn("Found", msg)
        self.assertIn("Gift Sets", msg)

        # 2. Direct category alias: combos
        mock_send.reset_mock()
        payload = make_webhook_payload("combos", sender=self.sender)
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)

        mock_send.assert_called_once()
        msg = mock_send.call_args[1].get("message") or mock_send.call_args[0][1]
        self.assertIn("Found", msg)
        self.assertIn("Combos", msg)

        # 3. Direct category alias: mugs
        mock_send.reset_mock()
        payload = make_webhook_payload("mugs", sender=self.sender)
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)

        mock_send.assert_called_once()
        msg = mock_send.call_args[1].get("message") or mock_send.call_args[0][1]
        self.assertTrue("Reply with" in msg or "Found" in msg)

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_5_full_multi_turn_browsing_flow(self, mock_send, mock_gemini):
        """
        Full end-to-end multi-turn conversation:
        Turn 1: "Categories" -> displays category menu
        Turn 2: "3" -> displays Gift Sets products
        Turn 3: "1" -> selects first product candidate
        Turn 4: "GS-001 100" -> receives price quote
        Turn 5: "CONFIRM" -> confirms order
        """
        # Turn 1: Categories
        res1 = self.client.post("/webhook", json=make_webhook_payload("Categories", sender=self.sender))
        self.assertEqual(res1.status_code, 200)
        msg1 = mock_send.call_args[1].get("message") or mock_send.call_args[0][1]
        self.assertIn("Product Categories", msg1)

        # Turn 2: Category number "3" (Gift Sets)
        mock_send.reset_mock()
        res2 = self.client.post("/webhook", json=make_webhook_payload("3", sender=self.sender))
        self.assertEqual(res2.status_code, 200)
        msg2 = mock_send.call_args[1].get("message") or mock_send.call_args[0][1]
        self.assertIn("Found", msg2)
        self.assertIn("Gift Sets", msg2)

        # Turn 3: Product candidate "1"
        mock_send.reset_mock()
        res3 = self.client.post("/webhook", json=make_webhook_payload("1", sender=self.sender))
        self.assertEqual(res3.status_code, 200)
        msg3 = mock_send.call_args[1].get("message") or mock_send.call_args[0][1]
        self.assertIn("You selected", msg3)
        self.assertIn("Please tell me the quantity you need", msg3)

        # Turn 4: Quantity "GS-001 100"
        mock_send.reset_mock()
        res4 = self.client.post("/webhook", json=make_webhook_payload("GS-001 100", sender=self.sender))
        self.assertEqual(res4.status_code, 200)
        msg4 = mock_send.call_args[1].get("message") or mock_send.call_args[0][1]
        self.assertIn("Quotation for GS-001", msg4)
        self.assertIn("100 units", msg4)
        self.assertIn("Grand Total:", msg4)

        # Turn 5: Confirm
        mock_send.reset_mock()
        res5 = self.client.post("/webhook", json=make_webhook_payload("CONFIRM", sender=self.sender))
        self.assertEqual(res5.status_code, 200)
        msg5 = mock_send.call_args[1].get("message") or mock_send.call_args[0][1]
        self.assertIn("Order Confirmed", msg5)

    def test_6_categories_never_reaches_keyword_search(self):
        """Explicitly verifies 'Categories' never executes product keyword search."""
        with patch.object(catalogue_service, "search_products", wraps=catalogue_service.search_products) as spy_search:
            reply = agent_router.handle_incoming_message(self.sender, "Categories")

            # Check that search_products was NOT called with query="Categories"
            for call in spy_search.call_args_list:
                _, kwargs = call
                self.assertNotEqual(kwargs.get("query"), "Categories")
                self.assertNotEqual(kwargs.get("query"), "categories")

            # Check reply is the category menu
            self.assertIn("Product Categories", reply)
            self.assertNotIn("No products found matching", reply)


if __name__ == "__main__":
    unittest.main()
