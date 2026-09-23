import os
import sys
import unittest
import uuid
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

# Ensure backend root is on sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from main import app
from services.catalogue_service import catalogue_service

def make_webhook_payload(body_text: str, sender: str = "919876543210", message_id: str = None):
    """Helper to generate a Meta Cloud API incoming message webhook payload."""
    msg_id = message_id or f"wamid.test_{uuid.uuid4().hex[:12]}"
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
                                "display_phone_number": "15551387741",
                                "phone_number_id": "2096262090982740"
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Test Customer"},
                                    "wa_id": sender
                                }
                            ],
                            "messages": [
                                {
                                    "from": sender,
                                    "id": msg_id,
                                    "timestamp": "1726750000",
                                    "text": {"body": body_text},
                                    "type": "text"
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }

def make_status_payload():
    """Helper to generate a Meta Cloud API delivery status webhook payload."""
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
                                "display_phone_number": "15551387741",
                                "phone_number_id": "2096262090982740"
                            },
                            "statuses": [
                                {
                                    "id": "wamid.HBgMOTE5ODc2NTQzMjEwFQIAEhgUM0EBQ0RF",
                                    "status": "delivered",
                                    "timestamp": "1726750005",
                                    "recipient_id": "919876543210"
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }

class TestWebhookCatalogueIntegration(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Mock gemini service as unavailable so catalogue-level tests remain deterministic
        self.patcher = patch("services.agent_router.gemini_service.is_available", return_value=False)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_1_exact_sku_lookup(self, mock_send):
        payload = make_webhook_payload("XG-501")
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertIn("XG-501", msg)
        self.assertIn("Combos", msg)
        self.assertIn("Based on quantity", msg)
        self.assertIn("Please tell me the quantity you need", msg)

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_2_lowercase_sku(self, mock_send):
        payload = make_webhook_payload("xg-501")
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertIn("XG-501", msg)
        self.assertIn("Combos", msg)

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_3_sku_without_punctuation(self, mock_send):
        payload = make_webhook_payload("XG501")
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertIn("XG-501", msg)

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_4_category_requests(self, mock_send):
        categories_to_test = [
            ("mugs", "Mugs & Drinkware"),
            ("bottles", "Water Bottles"),
            ("gift sets", "Gift Sets"),
            ("combos", "Combos"),
            ("electronics", "Electronics"),
            ("pens", "Writing Instruments"),
            ("notebooks", "Notebooks")
        ]
        for query, expected_cat in categories_to_test:
            mock_send.reset_mock()
            payload = make_webhook_payload(query)
            response = self.client.post("/webhook", json=payload)
            self.assertEqual(response.status_code, 200)
            mock_send.assert_called_once()
            args, kwargs = mock_send.call_args
            msg = kwargs.get("message") or args[1]
            self.assertTrue(expected_cat in msg or "Reply with" in msg)

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_5_categories_command(self, mock_send):
        payload = make_webhook_payload("categories")
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertIn("Mudhra Branding Solutions — Product Categories", msg)
        self.assertIn("Gift Sets", msg)
        self.assertIn("Combos", msg)
        self.assertIn("Notebooks", msg)

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_6_keyword_search(self, mock_send):
        payload = make_webhook_payload("bamboo")
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertIn("Results for 'bamboo'", msg)

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_7_unknown_query(self, mock_send):
        payload = make_webhook_payload("xyz unknown product")
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertIn("No products found matching \"xyz unknown product\"", msg)
        self.assertIn("Try a category", msg)

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_8_unsupported_empty_message(self, mock_send):
        payload = make_webhook_payload("   ")
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertIn("Welcome to *Mudhra Branding Solutions*", msg)
        self.assertIn("Categories", msg)

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_9_status_event_does_not_trigger_response(self, mock_send):
        payload = make_status_payload()
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)
        # Verify no customer message is sent for delivery status updates
        mock_send.assert_not_called()

    def test_10_webhook_verification_endpoint(self):
        with patch.dict(os.environ, {"WHATSAPP_VERIFY_TOKEN": "test_verify_token"}):
            # Successful verification
            response = self.client.get(
                "/webhook?hub.mode=subscribe&hub.verify_token=test_verify_token&hub.challenge=challenge_token_999"
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text, "challenge_token_999")

            # Invalid token rejection
            response_bad = self.client.get(
                "/webhook?hub.mode=subscribe&hub.verify_token=WRONG_TOKEN&hub.challenge=challenge_token_999"
            )
            self.assertEqual(response_bad.status_code, 403)

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_11_pricing_request_unconfigured(self, mock_send):
        # Case: 'XG-577 100' (XG-577 is an unconfigured combo SKU)
        payload1 = make_webhook_payload("XG-577 100")
        response1 = self.client.post("/webhook", json=payload1)
        self.assertEqual(response1.status_code, 200)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        msg1 = kwargs.get("message") or args[1]
        self.assertEqual(msg1, "Pricing for XG-577 is not configured yet. Please contact sales.")

        # Case: '100 XG-577'
        mock_send.reset_mock()
        payload2 = make_webhook_payload("100 XG-577")
        response2 = self.client.post("/webhook", json=payload2)
        self.assertEqual(response2.status_code, 200)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        msg2 = kwargs.get("message") or args[1]
        self.assertEqual(msg2, "Pricing for XG-577 is not configured yet. Please contact sales.")

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_12_pricing_request_configured(self, mock_send):
        payload = make_webhook_payload("XG-501 100")
        response = self.client.post("/webhook", json=payload)
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertIn("Quotation for XG-501", msg)
        self.assertIn("100 units", msg)
        self.assertIn("410.00", msg)
        self.assertIn("48,380.00", msg)

if __name__ == "__main__":
    unittest.main()
