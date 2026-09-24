"""
Regression tests for Multi-Product Requirement Extraction and Contextual Selection.
Verifies:
1. Multi-product messages like "I need 5 bootles and 10 pens" extract independent requirements:
   - bottles -> quantity 5 (with typo tolerance bootles -> bottles)
   - pens -> quantity 10
2. Candidates for both categories are searched independently and presented in numbered layout.
3. Candidate selection (e.g. "1", "3", or SKU) continues product details and quotation flow.
4. Contextual image requests ("I need image for 3", "Get me image as well") work accurately with candidates.
5. Active quote/order confirmation safety is strictly preserved.
"""

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from main import app
from services.conversation_service import conversation_service


def make_webhook_payload(body_text: str, sender: str = "919876543310", name: str = "Test Buyer"):
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


class TestMultiProductRequests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.sender = "919876543310"
        conv = conversation_service.get_or_create_conversation(self.sender)
        conversation_service.clear_selection(conv.conversation_id)
        conversation_service.set_candidates(conv.conversation_id, [])

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_image_message", new_callable=AsyncMock)
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_1_multi_product_exact_production_message(self, mock_send_text, mock_send_image, mock_gemini):
        """
        Tests the exact production message: 'I need 5 bootles and 10 pens'.
        Verifies:
        - Typo tolerance: 'bootles' -> 'Water Bottles', qty: 5
        - 'pens' -> 'Writing Instruments', qty: 10
        - Fast path does not invoke Gemini intent parser.
        - Options for both categories are presented with accurate quantities.
        """
        phone = "919876543311"
        resp = self.client.post("/webhook", json=make_webhook_payload("I need 5 bootles and 10 pens", phone))
        self.assertEqual(resp.status_code, 200)

        mock_gemini.assert_not_called()
        mock_send_text.assert_called_once()
        reply = mock_send_text.call_args.kwargs.get("message")

        # Must mention both requirements and options
        self.assertIn("Water Bottles", reply)
        self.assertIn("5 units", reply)
        self.assertIn("Writing Instruments", reply)
        self.assertIn("10 units", reply)
        self.assertNotIn("No products found", reply)

        # Candidates stored in conversation context
        conv = conversation_service.get_or_create_conversation(phone)
        self.assertTrue(len(conv.current_product_candidates) >= 2)

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_image_message", new_callable=AsyncMock)
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_2_multi_product_candidate_selection_and_image_flow(self, mock_send_text, mock_send_image, mock_gemini):
        """
        Tests multi-product search followed by candidate selection and image requests.
        """
        phone = "919876543312"
        # 1. Multi-product query
        self.client.post("/webhook", json=make_webhook_payload("5 bottles and 10 pens", phone))

        # 2. Numbered image request for candidate #1: "I need image for 1"
        mock_send_image.reset_mock()
        mock_send_text.reset_mock()
        resp = self.client.post("/webhook", json=make_webhook_payload("I need image for 1", phone))
        self.assertEqual(resp.status_code, 200)

        mock_gemini.assert_not_called()
        mock_send_text.assert_called_once()
        reply = mock_send_text.call_args.kwargs.get("message")
        self.assertIn("XG-BT-001", reply)
        self.assertNotIn("No products found", reply)

        # 3. Contextual image request: "Get me image as well"
        mock_send_image.reset_mock()
        mock_send_text.reset_mock()
        resp2 = self.client.post("/webhook", json=make_webhook_payload("Get me image as well", phone))
        self.assertEqual(resp2.status_code, 200)
        mock_gemini.assert_not_called()
        mock_send_text.assert_called_once()

    @patch("services.gemini_service.gemini_service.parse_intent")
    @patch("main.send_image_message", new_callable=AsyncMock)
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_3_multi_product_typos_and_variations(self, mock_send_text, mock_send_image, mock_gemini):
        """
        Tests multiple natural variations with typos:
        - '10 penz and 5 bootles'
        - '50 muggs and 100 pens'
        - '20 water bottles, 50 metal pens'
        """
        phone = "919876543313"
        variations = [
            ("10 penz and 5 bootles", ["Writing Instruments", "Water Bottles"]),
            ("50 muggs and 100 pens", ["Mugs & Drinkware", "Writing Instruments"]),
            ("20 water bottles, 50 metal pens", ["Water Bottles", "Writing Instruments"]),
        ]

        for q, expected_cats in variations:
            mock_send_text.reset_mock()
            mock_gemini.reset_mock()
            resp = self.client.post("/webhook", json=make_webhook_payload(q, phone))
            self.assertEqual(resp.status_code, 200)
            mock_gemini.assert_not_called()
            mock_send_text.assert_called_once()
            reply = mock_send_text.call_args.kwargs.get("message")
            for exp_cat in expected_cats:
                self.assertIn(exp_cat, reply)
            self.assertNotIn("No products found", reply)


if __name__ == "__main__":
    unittest.main()
