import os
import sys
import unittest
import uuid
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from main import app
from services.conversation_service import conversation_service
from services.order_service import order_service


def make_webhook_payload(body_text: str, message_id: str, sender: str = "919876543210"):
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
                                    "id": message_id,
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


class TestWebhookIdempotency(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.patcher = patch("services.agent_router.gemini_service.is_available", return_value=False)
        self.patcher.start()
        # Clear in-memory cache between tests
        if hasattr(conversation_service, "_dedup_cache"):
            conversation_service._dedup_cache.clear()

    def tearDown(self):
        self.patcher.stop()

    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("main.send_image_message", new_callable=AsyncMock)
    def test_same_wamid_received_twice_only_one_reply(self, mock_send_img, mock_send_txt):
        """Requirement C: Same wamid received twice -> only one processing/reply."""
        wamid = f"wamid.TEST_IDEMPOTENCY_{uuid.uuid4().hex[:8]}"
        payload = make_webhook_payload("XG-501", message_id=wamid)

        # First delivery from Meta
        res1 = self.client.post("/webhook", json=payload)
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(mock_send_txt.call_count, 1)

        # Immediate Meta retry of the exact same message
        res2 = self.client.post("/webhook", json=payload)
        self.assertEqual(res2.status_code, 200)
        # Call count must still be 1 (duplicate skipped)
        self.assertEqual(mock_send_txt.call_count, 1)

    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("main.send_image_message", new_callable=AsyncMock)
    def test_two_different_wamids_both_process_normally(self, mock_send_img, mock_send_txt):
        """Requirement D: Two different wamids -> both process normally."""
        wamid_1 = f"wamid.TEST_UNIQUE_{uuid.uuid4().hex[:8]}"
        wamid_2 = f"wamid.TEST_UNIQUE_{uuid.uuid4().hex[:8]}"

        res1 = self.client.post("/webhook", json=make_webhook_payload("XG-501", message_id=wamid_1))
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(mock_send_txt.call_count, 1)

        res2 = self.client.post("/webhook", json=make_webhook_payload("XG-502", message_id=wamid_2))
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(mock_send_txt.call_count, 2)

    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("main.send_image_message", new_callable=AsyncMock)
    def test_order_confirmation_retry_does_not_duplicate_order(self, mock_send_img, mock_send_txt):
        """Requirement E: Existing order confirmation behavior remains unchanged and idempotent."""
        sender = f"9199{uuid.uuid4().int % 100000000:08d}"
        wamid_quote = f"wamid.ORDER_SETUP_{uuid.uuid4().hex[:8]}"
        wamid_confirm = f"wamid.ORDER_CONFIRM_{uuid.uuid4().hex[:8]}"

        # 1. Ask for a quote
        res_quote = self.client.post(
            "/webhook", json=make_webhook_payload("XG-501 10", message_id=wamid_quote, sender=sender)
        )
        self.assertEqual(res_quote.status_code, 200)
        self.assertEqual(mock_send_txt.call_count, 1)

        # 2. Confirm order
        res_confirm1 = self.client.post(
            "/webhook", json=make_webhook_payload("CONFIRM", message_id=wamid_confirm, sender=sender)
        )
        self.assertEqual(res_confirm1.status_code, 200)
        self.assertEqual(mock_send_txt.call_count, 2)

        # Count orders for this customer
        orders_before = order_service.list_customer_orders(sender)

        # 3. Meta retry of the exact same confirmation webhook
        res_confirm2 = self.client.post(
            "/webhook", json=make_webhook_payload("CONFIRM", message_id=wamid_confirm, sender=sender)
        )
        self.assertEqual(res_confirm2.status_code, 200)
        # Should NOT send another reply
        self.assertEqual(mock_send_txt.call_count, 2)

        # Should NOT create another order
        orders_after = order_service.list_customer_orders(sender)
        self.assertEqual(len(orders_before), len(orders_after))


if __name__ == "__main__":
    unittest.main()
