import json
import os
import re
import sys
import unittest
from unittest.mock import AsyncMock, patch

# Ensure backend root is on sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi.testclient import TestClient
from main import app
from services.agent_router import agent_router
from services.catalogue_service import catalogue_service
from services.conversation_models import MessageDirection
from services.conversation_service import conversation_service
from services.gemini_models import IntentType, StructuredIntent
from services.order_models import OrderStatus
from services.order_service import order_service
from services.pricing_service import PriceRule, pricing_service


def make_webhook_payload(body_text: str, sender: str = "919811111111", name: str = "Corporate Buyer"):
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
                                "phone_number_id": "2096262090982740",
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
                                    "id": f"wamid.{sender}_{abs(hash(body_text))}",
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


class TestProductSelectionExperience(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        pricing_service.load_pricing_master()

    def _clean_test_state(self, phone: str):
        conv = conversation_service.get_or_create_conversation(phone)
        conversation_service.clear_selection(conv.conversation_id)
        conversation_service.clear_pending_quote(conv.conversation_id)
        order_service.clear_pending_quote(phone)
        with order_service._get_connection() as conn:
            conn.execute(
                "DELETE FROM order_items WHERE order_id IN (SELECT order_id FROM orders WHERE customer_phone = ?)",
                (phone,),
            )
            conn.execute("DELETE FROM orders WHERE customer_phone = ?", (phone,))
            conn.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = ?",
                (conv.conversation_id,),
            )
            conn.commit()

    # =========================================================================
    # Test 1: Flow with Selection by Numeric Index "1"
    # =========================================================================
    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("services.agent_router.gemini_service")
    def test_selection_by_numeric_index_1(self, mock_gemini, mock_send_msg):
        phone = "919811111101"
        self._clean_test_state(phone)
        mock_gemini.is_available.return_value = True

        # Turn 1: Search query
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category="Gift Sets",
            quantity=100,
            budget_per_unit=500.0,
        )
        resp1 = self.client.post(
            "/webhook",
            json=make_webhook_payload("I need 100 gift sets around ₹500", sender=phone),
        )
        self.assertEqual(resp1.status_code, 200)
        msg1 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Found 5 matching products", msg1)
        self.assertIn("1️⃣", msg1)
        self.assertIn("5️⃣", msg1)

        conv = conversation_service.get_or_create_conversation(phone)
        self.assertEqual(len(conv.current_product_candidates), 5)
        candidate_1 = conv.current_product_candidates[0]
        sku_1 = candidate_1["sku"]
        self.assertEqual(conv.selected_quantity, 100)

        # Turn 2: Customer selects "1"
        resp2 = self.client.post("/webhook", json=make_webhook_payload("1", sender=phone))
        self.assertEqual(resp2.status_code, 200)
        msg2 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn(f"Quotation for {sku_1}", msg2)
        self.assertIn("100 units", msg2)
        self.assertIn("Subtotal:", msg2)
        self.assertIn("Grand Total:", msg2)
        self.assertIn("Price shown is based on the current catalogue pricing", msg2)
        self.assertNotIn("Availability confirmation required", msg2)

        # Turn 3: Customer confirms order
        resp3 = self.client.post("/webhook", json=make_webhook_payload("confirm", sender=phone))
        self.assertEqual(resp3.status_code, 200)
        msg3 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Order Confirmed!", msg3)

        orders = order_service.list_customer_orders(phone)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].items[0].sku, sku_1)
        self.assertEqual(orders[0].items[0].quantity, 100)
        self.assertEqual(orders[0].status, OrderStatus.CONFIRMED)

    # =========================================================================
    # Test 2: Flow with Selection by Numeric Index "2"
    # =========================================================================
    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("services.agent_router.gemini_service")
    def test_selection_by_numeric_index_2(self, mock_gemini, mock_send_msg):
        phone = "919811111102"
        self._clean_test_state(phone)
        mock_gemini.is_available.return_value = True

        # Turn 1: Search query
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category="Gift Sets",
            quantity=100,
            budget_per_unit=500.0,
        )
        self.client.post(
            "/webhook",
            json=make_webhook_payload("I need 100 gift sets around ₹500", sender=phone),
        )

        conv = conversation_service.get_or_create_conversation(phone)
        candidate_2 = conv.current_product_candidates[1]
        sku_2 = candidate_2["sku"]
        self.assertEqual(sku_2, "GS-002")

        # Turn 2: Customer selects "2"
        resp2 = self.client.post("/webhook", json=make_webhook_payload("2", sender=phone))
        self.assertEqual(resp2.status_code, 200)
        msg2 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn(f"Quotation for {sku_2}", msg2)
        self.assertIn("100 units", msg2)
        self.assertIn("Grand Total:", msg2)
        self.assertIn("Price shown is based on the current catalogue pricing", msg2)
        self.assertNotIn("Availability confirmation required", msg2)
        # Ensure canonical SKU is used, not space-padded raw sheet string
        self.assertNotIn("XG - GS - 002", msg2)

        # Turn 3: Customer confirms order
        resp3 = self.client.post("/webhook", json=make_webhook_payload("confirm", sender=phone))
        self.assertEqual(resp3.status_code, 200)
        msg3 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Order Confirmed!", msg3)

        orders = order_service.list_customer_orders(phone)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].items[0].sku, "GS-002")
        self.assertEqual(orders[0].items[0].quantity, 100)

    # =========================================================================
    # Test 3: Flow with Selection by Ordinal Phrase "second one"
    # =========================================================================
    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("services.agent_router.gemini_service")
    def test_selection_by_ordinal_phrase_second_one(self, mock_gemini, mock_send_msg):
        phone = "919811111103"
        self._clean_test_state(phone)
        mock_gemini.is_available.return_value = True

        # Turn 1: Search query
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category="Gift Sets",
            quantity=100,
            budget_per_unit=500.0,
        )
        self.client.post(
            "/webhook",
            json=make_webhook_payload("I need 100 gift sets around ₹500", sender=phone),
        )

        # Turn 2: Customer selects "second one"
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.SELECT_PRODUCT,
            selection_index=2,
        )
        resp2 = self.client.post("/webhook", json=make_webhook_payload("second one", sender=phone))
        self.assertEqual(resp2.status_code, 200)
        msg2 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Quotation for GS-002", msg2)
        self.assertIn("100 units", msg2)
        self.assertIn("Price shown is based on the current catalogue pricing", msg2)
        self.assertNotIn("Availability confirmation required", msg2)

        # Turn 3: Customer confirms order
        resp3 = self.client.post("/webhook", json=make_webhook_payload("confirm", sender=phone))
        self.assertEqual(resp3.status_code, 200)
        msg3 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Order Confirmed!", msg3)

        orders = order_service.list_customer_orders(phone)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].items[0].sku, "GS-002")

    # =========================================================================
    # Test 4: Flow with Selection by Canonical SKU "GS-002"
    # =========================================================================
    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("services.agent_router.gemini_service")
    def test_selection_by_canonical_sku(self, mock_gemini, mock_send_msg):
        phone = "919811111104"
        self._clean_test_state(phone)
        mock_gemini.is_available.return_value = True

        # Turn 1: Search query remembers quantity 100
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category="Gift Sets",
            quantity=100,
            budget_per_unit=500.0,
        )
        self.client.post(
            "/webhook",
            json=make_webhook_payload("I need 100 gift sets around ₹500", sender=phone),
        )

        # Turn 2: Customer selects by typing "GS-002"
        resp2 = self.client.post("/webhook", json=make_webhook_payload("GS-002", sender=phone))
        self.assertEqual(resp2.status_code, 200)
        msg2 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Quotation for GS-002", msg2)
        self.assertIn("100 units", msg2)
        self.assertIn("Price shown is based on the current catalogue pricing", msg2)
        self.assertNotIn("Availability confirmation required", msg2)

        # Turn 3: Customer confirms order
        resp3 = self.client.post("/webhook", json=make_webhook_payload("confirm", sender=phone))
        self.assertEqual(resp3.status_code, 200)
        msg3 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Order Confirmed!", msg3)

        orders = order_service.list_customer_orders(phone)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].items[0].sku, "GS-002")
        self.assertEqual(orders[0].items[0].quantity, 100)

    # =========================================================================
    # Test 5: Duplicate Confirmation Protection
    # =========================================================================
    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("services.agent_router.gemini_service")
    def test_duplicate_confirmation_creates_single_order(self, mock_gemini, mock_send_msg):
        phone = "919811111105"
        self._clean_test_state(phone)
        mock_gemini.is_available.return_value = True

        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category="Gift Sets",
            quantity=100,
        )
        self.client.post("/webhook", json=make_webhook_payload("I need 100 gift sets", sender=phone))
        self.client.post("/webhook", json=make_webhook_payload("2", sender=phone))

        # First confirmation
        resp_c1 = self.client.post("/webhook", json=make_webhook_payload("confirm", sender=phone))
        self.assertEqual(resp_c1.status_code, 200)
        self.assertIn("Order Confirmed!", mock_send_msg.call_args[1].get("message", ""))

        orders_after_first = order_service.list_customer_orders(phone)
        self.assertEqual(len(orders_after_first), 1)

        # Second duplicate confirmation
        resp_c2 = self.client.post("/webhook", json=make_webhook_payload("confirm", sender=phone))
        self.assertEqual(resp_c2.status_code, 200)
        self.assertIn("don't have an active quotation pending confirmation", mock_send_msg.call_args[1].get("message", ""))

        orders_after_second = order_service.list_customer_orders(phone)
        self.assertEqual(len(orders_after_second), 1)


if __name__ == "__main__":
    unittest.main()
