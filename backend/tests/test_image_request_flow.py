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
from services.catalogue_service import catalogue_service, is_image_request_intent, is_category_browsing_intent
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
                                    "id": f"wamid.test_{abs(hash(body_text))}",
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


class TestImageRequestFlow(unittest.TestCase):
    """
    Regression test suite for WhatsApp product image requests.
    Verifies:
    1. Image request with current candidates having images dispatches real Supabase URLs via send_image_message.
    2. Multiple natural image request phrasings are supported deterministically without Gemini.
    3. Image request with candidates lacking images does not fabricate URLs and clearly indicates availability.
    4. Image request without current candidates returns graceful guidance rather than a keyword search.
    5. Candidate list and quantities are preserved so selection (1, 2, second one, SKU) continues quotation flow.
    6. Image requests never become product keyword searches.
    7. WhatsApp media dispatch path uses agent_router -> pending media -> main.py -> send_image_message().
    """

    def setUp(self):
        self.client = TestClient(app)
        self.sender = "919876543210"
        # Reset conversation state
        conv = conversation_service.get_or_create_conversation(self.sender)
        conversation_service.clear_selection(conv.conversation_id)
        conversation_service.set_candidates(conv.conversation_id, [])

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_image_message", new_callable=AsyncMock)
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_1_image_request_with_candidates_having_images(self, mock_send_text, mock_send_image, mock_gemini):
        """When candidates have images, image request dispatches real Supabase URLs and preserves candidates."""
        # Step 1: Browse Categories
        self.client.post("/webhook", json=make_webhook_payload("Categories", self.sender))
        
        # Step 2: Select Electronics (Category 2, has 5 real images in Supabase)
        self.client.post("/webhook", json=make_webhook_payload("2", self.sender))
        conv = conversation_service.get_or_create_conversation(self.sender)
        self.assertEqual(len(conv.current_product_candidates), 5)
        
        # Reset mocks before image request
        mock_send_image.reset_mock()
        mock_send_text.reset_mock()
        mock_gemini.reset_mock()

        # Step 3: Request images
        resp = self.client.post("/webhook", json=make_webhook_payload("can you show me image", self.sender))
        self.assertEqual(resp.status_code, 200)

        # Assert Gemini was NOT called
        mock_gemini.assert_not_called()

        # Assert WhatsApp send_image_message was called for candidate images
        self.assertGreaterEqual(mock_send_image.call_count, 1)
        for call_args in mock_send_image.call_args_list:
            to = call_args.kwargs.get("to")
            img_url = call_args.kwargs.get("image_url")
            caption = call_args.kwargs.get("caption")
            self.assertEqual(to, self.sender)
            self.assertTrue(img_url.startswith("http"), f"Image URL must be valid HTTP URL: {img_url}")
            self.assertIn("supabase.co", img_url, "Must use real Supabase Storage URL")
            self.assertIn("SKU:", caption, "Caption must identify SKU")
            self.assertTrue(any(b in caption for b in ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]) or "Options:" in caption, "Caption must have index badge or details")

        # Assert candidates are still preserved
        conv = conversation_service.get_or_create_conversation(self.sender)
        self.assertEqual(len(conv.current_product_candidates), 5)

        # Assert text message was also sent and does not say 'No products found'
        mock_send_text.assert_called_once()
        reply_sent = mock_send_text.call_args.kwargs.get("message")
        self.assertNotIn("No products found", reply_sent)

    def test_2_multiple_image_request_phrasings_intent_detection(self):
        """Deterministic intent detection matches all natural image/photo request variations."""
        phrasings = [
            "can you show me image",
            "show me images",
            "show images",
            "show pictures",
            "can I see the images",
            "can I see photos",
            "show me photos",
            "product images",
            "show product image",
            "can you show the picture",
            "what does it look like",
            "can I see them",
            "*can you show me image?*",
            "_show me images_",
            "Can you please send photos?",
            "could you share pictures",
            "do you have photos?",
            "how does it look",
            "what do they look like?",
            "i want to see photos",
            "can we see images",
            "show pics",
            "any photos",
            "send me pictures",
            "pictures",
            "photos",
            "images",
            "image",
        ]
        for phrase in phrasings:
            self.assertTrue(
                is_image_request_intent(phrase),
                f"Failed to recognize image request phrasing: '{phrase}'"
            )

        # Ensure no false positives for non-image inputs
        non_image_inputs = [
            "Categories",
            "1",
            "2",
            "second one",
            "XG-501",
            "GS-001 100",
            "Combos",
            "Mugs",
            "Minimal Bamboo powerbank",
            "hi",
            "yes",
            "CONFIRM",
        ]
        for non_phrase in non_image_inputs:
            self.assertFalse(
                is_image_request_intent(non_phrase),
                f"False positive image request for: '{non_phrase}'"
            )

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_image_message", new_callable=AsyncMock)
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_3_image_request_with_candidates_without_images(self, mock_send_text, mock_send_image, mock_gemini):
        """When candidates lack images (e.g. Combos XG-501..XG-505), no fake URLs are sent, status is communicated clearly."""
        # Step 1: Browse Categories
        self.client.post("/webhook", json=make_webhook_payload("Categories", self.sender))

        # Step 2: Select Combos (Category 1, 0 images in catalogue)
        self.client.post("/webhook", json=make_webhook_payload("1", self.sender))
        conv = conversation_service.get_or_create_conversation(self.sender)
        self.assertEqual(len(conv.current_product_candidates), 5)
        self.assertEqual(conv.current_product_candidates[0]["sku"], "XG-501")

        mock_send_image.reset_mock()
        mock_send_text.reset_mock()
        mock_gemini.reset_mock()

        # Step 3: Ask for images
        resp = self.client.post("/webhook", json=make_webhook_payload("can you show me image", self.sender))
        self.assertEqual(resp.status_code, 200)

        # Assert no fake image messages dispatched
        mock_send_image.assert_not_called()
        mock_gemini.assert_not_called()

        # Assert outbound text politely informs customer that photos are not currently on file
        mock_send_text.assert_called_once()
        reply_sent = mock_send_text.call_args.kwargs.get("message")
        self.assertIn("Photos are not currently on file", reply_sent)
        self.assertIn("XG-501", reply_sent)
        self.assertNotIn("No products found", reply_sent)

        # Candidates must remain preserved!
        conv = conversation_service.get_or_create_conversation(self.sender)
        self.assertEqual(len(conv.current_product_candidates), 5)

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_image_message", new_callable=AsyncMock)
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_4_image_request_without_candidates_returns_graceful_guidance(self, mock_send_text, mock_send_image, mock_gemini):
        """Without active candidates, image request returns guidance instead of performing a product keyword search."""
        conv = conversation_service.get_or_create_conversation(self.sender)
        conversation_service.set_candidates(conv.conversation_id, [])

        resp = self.client.post("/webhook", json=make_webhook_payload("show me images", self.sender))
        self.assertEqual(resp.status_code, 200)

        mock_gemini.assert_not_called()
        mock_send_image.assert_not_called()
        mock_send_text.assert_called_once()

        reply_sent = mock_send_text.call_args.kwargs.get("message")
        self.assertIn("Sure — tell me the product or category you'd like to see images for", reply_sent)
        self.assertNotIn("No products found", reply_sent)

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_5_candidate_selection_after_image_request(self, mock_send_text, mock_gemini):
        """Customer can select a product candidate by index, ordinal, or SKU immediately after requesting images."""
        # 1. Setup with Combos: Categories -> 1 (Combos) -> 5 candidates
        self.client.post("/webhook", json=make_webhook_payload("Categories", self.sender))
        self.client.post("/webhook", json=make_webhook_payload("1", self.sender))

        # 2. Customer asks for image
        self.client.post("/webhook", json=make_webhook_payload("can you show me image", self.sender))

        # 3. Customer selects option '1' (XG-501)
        mock_send_text.reset_mock()
        self.client.post("/webhook", json=make_webhook_payload("1", self.sender))
        conv = conversation_service.get_or_create_conversation(self.sender)
        self.assertEqual(conv.selected_sku, "XG-501")
        reply_sent = mock_send_text.call_args.kwargs.get("message")
        self.assertIn("You selected", reply_sent)
        self.assertIn("XG-501", reply_sent)
        self.assertIn("quantity", reply_sent.lower())

        # 4. Also verify selection by ordinal works: "second one" -> XG-502
        self.client.post("/webhook", json=make_webhook_payload("Categories", self.sender))
        self.client.post("/webhook", json=make_webhook_payload("1", self.sender))
        self.client.post("/webhook", json=make_webhook_payload("show images", self.sender))
        self.client.post("/webhook", json=make_webhook_payload("second one", self.sender))
        conv = conversation_service.get_or_create_conversation(self.sender)
        self.assertEqual(conv.selected_sku, "XG-502")

        # 5. Also verify selection by direct candidate SKU works: "XG-503" -> XG-503
        self.client.post("/webhook", json=make_webhook_payload("show images", self.sender))
        self.client.post("/webhook", json=make_webhook_payload("XG-503", self.sender))
        conv = conversation_service.get_or_create_conversation(self.sender)
        self.assertEqual(conv.selected_sku, "XG-503")

        # 6. Verify quotation flow continues after selecting a priced candidate (Category 3: Gift Sets -> GS-001)
        self.client.post("/webhook", json=make_webhook_payload("Categories", self.sender))
        self.client.post("/webhook", json=make_webhook_payload("3", self.sender))
        self.client.post("/webhook", json=make_webhook_payload("can you show me image", self.sender))
        self.client.post("/webhook", json=make_webhook_payload("1", self.sender))
        conv = conversation_service.get_or_create_conversation(self.sender)
        self.assertEqual(conv.selected_sku, "GS-001")

        # Customer sends GS-001 100 to get quotation
        self.client.post("/webhook", json=make_webhook_payload("GS-001 100", self.sender))
        conv = conversation_service.get_or_create_conversation(self.sender)
        self.assertIsNotNone(conv.pending_quote)
        sku_val = conv.pending_quote.get("sku") if isinstance(conv.pending_quote, dict) else conv.pending_quote.sku
        qty_val = conv.pending_quote.get("quantity") if isinstance(conv.pending_quote, dict) else conv.pending_quote.quantity
        self.assertEqual(sku_val, "GS-001")
        self.assertEqual(qty_val, 100)

    @patch("services.catalogue_service.catalogue_service.search_products")
    @patch("services.gemini_service.gemini_service.parse_intent")
    def test_6_image_request_never_becomes_product_keyword_search(self, mock_gemini, mock_search):
        """Verifies search_products is never called with the image request query string."""
        phone = "919876543210"
        conv = conversation_service.get_or_create_conversation(phone)
        conversation_service.set_candidates(conv.conversation_id, [
            {"sku": "XG-501", "name": "Combos (3 in 1)", "category": "Combos"}
        ])

        # Test various image queries
        queries = ["can you show me image", "show me images", "product images", "can I see photos"]
        for q in queries:
            mock_search.reset_mock()
            reply = agent_router.handle_incoming_message(phone, q)
            # Ensure search_products was not called with this query
            for call_args in mock_search.call_args_list:
                called_query = call_args.kwargs.get("query")
                self.assertNotEqual(called_query, q, f"search_products should not be called with query='{q}'")
            self.assertNotIn("No products found", reply)


if __name__ == "__main__":
    unittest.main()
