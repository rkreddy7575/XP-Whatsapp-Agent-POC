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


    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_image_message", new_callable=AsyncMock)
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_7_contextual_image_request_natural_variations_and_typos(self, mock_send_text, mock_send_image, mock_gemini):
        """
        Tests contextual image requests with natural variations and typos, including:
        'can i hae image for thuis', 'can i have image for this', 'can I get an image',
        'show me image', 'show photo', 'can you send photo', 'image for this', 'show phoot', 'can you send imaeg'.
        Verifies image URL is sent for product with image (e.g. XG-BT-001) and graceful unavailable message for product without image (e.g. XG-MP-01).
        Never falls back to generic catalogue search.
        """
        phone = "919876543222"
        conv = conversation_service.get_or_create_conversation(phone)

        variations = [
            "can i hae image for thuis",
            "can i have image for this",
            "can I get an image",
            "show me image",
            "show photo",
            "can you send photo",
            "image for this",
            "show phoot",
            "can you send imaeg",
        ]

        for q in variations:
            conversation_service.set_selected_product(conv.conversation_id, "XG-BT-001", None)
            mock_send_image.reset_mock()
            mock_send_text.reset_mock()
            mock_gemini.reset_mock()

            resp = self.client.post("/webhook", json=make_webhook_payload(q, phone))
            self.assertEqual(resp.status_code, 200)

            # Gemini intent parser should NOT be called (deterministic fast path)
            mock_gemini.assert_not_called()

            # Image message should be dispatched for XG-BT-001
            mock_send_image.assert_called_once()
            img_url = mock_send_image.call_args.kwargs.get("image_url")
            self.assertIsNotNone(img_url)

            # Text message should confirm image is sent
            mock_send_text.assert_called_once()
            reply = mock_send_text.call_args.kwargs.get("message")
            self.assertIn("Image for XG-BT-001 is on its way", reply)
            self.assertNotIn("No products found", reply)

        # Also test product without image (e.g. XG-MP-01)
        conversation_service.set_selected_product(conv.conversation_id, "XG-MP-01", None)
        mock_send_image.reset_mock()
        mock_send_text.reset_mock()
        mock_gemini.reset_mock()
        resp = self.client.post("/webhook", json=make_webhook_payload("can i hae image for thuis", phone))
        self.assertEqual(resp.status_code, 200)
        mock_gemini.assert_not_called()
        mock_send_image.assert_not_called()
        mock_send_text.assert_called_once()
        reply_no_img = mock_send_text.call_args.kwargs.get("message")
        self.assertIn("image for XG-MP-01 yet", reply_no_img)
        self.assertNotIn("No products found", reply_no_img)

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_image_message", new_callable=AsyncMock)
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_8_contextual_image_request_preserves_quote_and_confirmation_state(self, mock_send_text, mock_send_image, mock_gemini):
        """
        Verifies that requesting an image when an active quote exists does NOT reset or corrupt
        the pending quote or confirmation flow. Customer can confirm right after receiving the image.
        """
        phone = "919876543233"
        # 1. Direct quote for GS-001 100
        self.client.post("/webhook", json=make_webhook_payload("GS-001 100", phone))
        conv = conversation_service.get_or_create_conversation(phone)
        self.assertIsNotNone(conv.pending_quote)
        self.assertEqual(conv.pending_quote.get("sku"), "GS-001")

        # 2. Customer asks "can i hae image for thuis"
        mock_send_image.reset_mock()
        mock_send_text.reset_mock()
        resp = self.client.post("/webhook", json=make_webhook_payload("can i hae image for thuis", phone))
        self.assertEqual(resp.status_code, 200)

        # Clear message explaining status
        reply = mock_send_text.call_args.kwargs.get("message")
        self.assertIn("CONFIRM", reply)
        self.assertNotIn("No products found", reply)

        # Quote must remain intact
        conv = conversation_service.get_or_create_conversation(phone)
        self.assertIsNotNone(conv.pending_quote)
        self.assertEqual(conv.pending_quote.get("sku"), "GS-001")
        self.assertEqual(conv.pending_quote.get("quantity"), 100)

        # 3. Customer confirms order
        mock_send_text.reset_mock()
        resp2 = self.client.post("/webhook", json=make_webhook_payload("CONFIRM", phone))
        self.assertEqual(resp2.status_code, 200)
        confirm_reply = mock_send_text.call_args.kwargs.get("message")
        self.assertIn("Order Confirmed", confirm_reply)
        self.assertIn("GS-001", confirm_reply)



    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_image_message", new_callable=AsyncMock)
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_9_candidate_numbered_image_request(self, mock_send_text, mock_send_image, mock_gemini):
        """
        Verifies that when candidates are displayed, numbered image requests such as:
        'I need image for 3', 'image for 3', 'show image 3', 'get image for 3'
        resolve against the current displayed candidate list (e.g. candidate #3), not global catalogue search.
        """
        phone = "919876543244"
        conv = conversation_service.get_or_create_conversation(phone)
        candidates = [
            {"sku": "XG-BT-001", "name": "Bottle 1", "category": "Water Bottles"},
            {"sku": "XG-MP-01", "name": "Pen 1", "category": "Writing Instruments"},
            {"sku": "XG-BT-003", "name": "Bottle 3", "category": "Water Bottles"},
        ]
        conversation_service.set_candidates(conv.conversation_id, candidates)

        image_variations = [
            "I need image for 3",
            "image for 3",
            "show image 3",
            "get image for 3",
        ]

        for q in image_variations:
            mock_send_image.reset_mock()
            mock_send_text.reset_mock()
            mock_gemini.reset_mock()

            resp = self.client.post("/webhook", json=make_webhook_payload(q, phone))
            self.assertEqual(resp.status_code, 200)

            mock_gemini.assert_not_called()
            mock_send_text.assert_called_once()
            reply = mock_send_text.call_args.kwargs.get("message")
            self.assertIn("XG-BT-003", reply)
            self.assertNotIn("No products found", reply)

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_image_message", new_callable=AsyncMock)
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_10_contextual_get_me_image_as_well(self, mock_send_text, mock_send_image, mock_gemini):
        """
        Verifies contextual image request 'Get me image as well' resolves against selected product / active quote.
        """
        phone = "919876543255"
        conv = conversation_service.get_or_create_conversation(phone)
        conversation_service.set_selected_product(conv.conversation_id, "XG-BT-001", None)

        mock_send_image.reset_mock()
        mock_send_text.reset_mock()
        mock_gemini.reset_mock()

        resp = self.client.post("/webhook", json=make_webhook_payload("Get me image as well", phone))
        self.assertEqual(resp.status_code, 200)

        mock_gemini.assert_not_called()
        mock_send_image.assert_called_once()
        mock_send_text.assert_called_once()
        reply = mock_send_text.call_args.kwargs.get("message")
        self.assertIn("Image for XG-BT-001 is on its way", reply)
        self.assertNotIn("No products found", reply)


    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_image_message", new_callable=AsyncMock)
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_11_uimage_and_extended_typo_variations(self, mock_send_text, mock_send_image, mock_gemini):
        """
        Tests extended typo handling for image requests, specifically verifying:
        'show me uimage', 'show me uimage for this', 'can i have uimage', 'show uimage',
        'send uimage', 'uimage please', 'show me imag', 'show me imaeg', 'show me imge',
        'show me foto', 'show me pht', 'show me piture', 'show me picutre'.
        Also verifies candidate numbered request 'uimage for 3'.
        """
        phone = "919876543266"
        conv = conversation_service.get_or_create_conversation(phone)
        conversation_service.set_selected_product(conv.conversation_id, "XG-BT-001", None)

        typo_variations = [
            "show me uimage",
            "show me uimage for this",
            "can i have uimage",
            "show uimage",
            "send uimage",
            "uimage please",
            "show me imag",
            "show me imaeg",
            "show me imge",
            "show me foto",
            "show me pht",
            "show me piture",
            "show me picutre",
        ]

        for q in typo_variations:
            mock_send_image.reset_mock()
            mock_send_text.reset_mock()
            mock_gemini.reset_mock()

            resp = self.client.post("/webhook", json=make_webhook_payload(q, phone))
            self.assertEqual(resp.status_code, 200)

            mock_gemini.assert_not_called()
            mock_send_image.assert_called_once()
            mock_send_text.assert_called_once()
            reply = mock_send_text.call_args.kwargs.get("message")
            self.assertIn("Image for XG-BT-001 is on its way", reply, f"Failed on typo variant: {q}")
            self.assertNotIn("No products found", reply, f"Fell through to search for: {q}")

        # Numbered candidate variation with uimage
        candidates = [
            {"sku": "XG-BT-001", "name": "Bottle 1", "category": "Water Bottles"},
            {"sku": "XG-MP-01", "name": "Pen 1", "category": "Writing Instruments"},
            {"sku": "XG-BT-003", "name": "Bottle 3", "category": "Water Bottles"},
        ]
        conversation_service.set_candidates(conv.conversation_id, candidates)
        mock_send_image.reset_mock()
        mock_send_text.reset_mock()
        mock_gemini.reset_mock()
        resp = self.client.post("/webhook", json=make_webhook_payload("uimage for 3", phone))
        self.assertEqual(resp.status_code, 200)
        mock_gemini.assert_not_called()
        mock_send_text.assert_called_once()
        reply_cand = mock_send_text.call_args.kwargs.get("message")
        self.assertIn("XG-BT-003", reply_cand)
        self.assertNotIn("No products found", reply_cand)

if __name__ == "__main__":

    unittest.main()
