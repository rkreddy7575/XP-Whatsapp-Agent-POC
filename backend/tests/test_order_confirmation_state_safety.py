import os
import unittest
import uuid
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from main import app
from services.agent_router import agent_router
from services.conversation_service import conversation_service
from services.order_service import order_service
from services.pricing_service import pricing_service, PriceQuoteResult
from services.gemini_models import IntentType, StructuredIntent


def _make_webhook_payload(body_text: str, sender: str, msg_id: str = None) -> dict:
    wamid = msg_id or f"wamid.test_{uuid.uuid4().hex[:12]}"
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
                                    "profile": {"name": "Test Customer"},
                                    "wa_id": sender,
                                }
                            ],
                            "messages": [
                                {
                                    "from": sender,
                                    "id": wamid,
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


def _fresh_phone() -> str:
    return f"91{uuid.uuid4().int % 10000000000:010d}"


class TestOrderConfirmationStateSafety(unittest.TestCase):
    """
    Mandatory State-Safety Test Suite:
    Proves that 'yes'/'okay'/'go ahead' CANNOT create an order unless the conversation
    has an active quotation and is in AWAITING_CONFIRMATION state.
    """

    @classmethod
    def setUpClass(cls):
        pricing_service.load_pricing_master()

    def setUp(self):
        self.client = TestClient(app)
        self.phone = _fresh_phone()
        self._clean_phone_state(self.phone)

    def tearDown(self):
        self._clean_phone_state(self.phone)

    def _clean_phone_state(self, phone: str):
        conv = conversation_service.get_or_create_conversation(phone)
        conversation_service.clear_selection(conv.conversation_id)
        conversation_service.clear_pending_quote(conv.conversation_id)
        conversation_service.set_candidates(conv.conversation_id, [])
        order_service.clear_pending_quote(phone)
        with order_service._get_connection() as conn:
            conn.execute(
                "DELETE FROM order_items WHERE order_id IN (SELECT order_id FROM orders WHERE customer_phone = ?)",
                (phone,),
            )
            conn.execute("DELETE FROM orders WHERE customer_phone = ?", (phone,))
            conn.commit()

    def _get_orders_for_phone(self, phone: str):
        return order_service.list_customer_orders(phone)

    # -------------------------------------------------------------------------
    # Test 1: PRODUCT_SELECTED + "yes" -> NO ORDER
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_1_product_selected_plus_yes_creates_no_order(self, mock_send):
        """When product is selected (no quote generated yet), sending 'yes' must create NO order."""
        conv = conversation_service.get_or_create_conversation(self.phone)
        conversation_service.set_selected_product(conv.conversation_id, "XG-MP-01", None)
        # Ensure no pending quote exists
        self.assertIsNone(order_service.get_pending_quote(self.phone))
        self.assertIsNone(conversation_service.get_pending_quote(conv.conversation_id))

        resp = self.client.post("/webhook", json=_make_webhook_payload("yes", self.phone))
        self.assertEqual(resp.status_code, 200)

        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 0, "No order should be created when only a product is selected")

        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertIn("don't have an active quotation", msg)
        self.assertNotIn("Order Confirmed!", msg)

    # -------------------------------------------------------------------------
    # Test 2: VIEWING_PRODUCT + "yes" -> NO ORDER
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_2_viewing_product_plus_yes_creates_no_order(self, mock_send):
        """When user is viewing product details via SKU query, sending 'yes' must create NO order."""
        # User views product
        self.client.post("/webhook", json=_make_webhook_payload("XG-MP-01", self.phone))
        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 0)

        # User sends "yes"
        mock_send.reset_mock()
        resp = self.client.post("/webhook", json=_make_webhook_payload("yes", self.phone))
        self.assertEqual(resp.status_code, 200)

        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 0, "No order should be created when viewing product")
        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertNotIn("Order Confirmed!", msg)

    # -------------------------------------------------------------------------
    # Test 3: AWAITING_QUANTITY + "yes" -> NO ORDER
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_3_awaiting_quantity_plus_yes_creates_no_order(self, mock_send):
        """When bot asked 'Please tell me the quantity you need', sending 'yes' must create NO order."""
        conv = conversation_service.get_or_create_conversation(self.phone)
        conversation_service.set_selected_product(conv.conversation_id, "GS-001", None)
        conv.last_intent = "SELECT_PRODUCT"

        resp = self.client.post("/webhook", json=_make_webhook_payload("yes", self.phone))
        self.assertEqual(resp.status_code, 200)

        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 0, "No order should be created when awaiting quantity")

    # -------------------------------------------------------------------------
    # Test 4: QUOTE_READY + "yes" -> NO ORDER unless bot explicitly generated quote
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_4_quote_ready_without_active_quote_creates_no_order(self, mock_send):
        """If conv state lacks active quote data, 'yes' must be rejected deterministically."""
        conv = conversation_service.get_or_create_conversation(self.phone)
        conv.last_intent = "PRICE_QUOTE"
        conversation_service.clear_pending_quote(conv.conversation_id)
        order_service.clear_pending_quote(self.phone)

        resp = self.client.post("/webhook", json=_make_webhook_payload("yes", self.phone))
        self.assertEqual(resp.status_code, 200)

        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 0, "No order without active quote")

    # -------------------------------------------------------------------------
    # Test 5: AWAITING_CONFIRMATION + "yes" -> exactly ONE order
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_5_awaiting_confirmation_plus_yes_creates_exactly_one_order(self, mock_send):
        """Active quote + 'yes' creates exactly 1 order."""
        # Generate active quote
        self.client.post("/webhook", json=_make_webhook_payload("XG-501 100", self.phone))
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 0)

        # Confirm with "yes"
        mock_send.reset_mock()
        resp = self.client.post("/webhook", json=_make_webhook_payload("yes", self.phone))
        self.assertEqual(resp.status_code, 200)

        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 1, "Must create exactly 1 order")
        self.assertEqual(orders[0].items[0].sku, "XG-501")
        self.assertEqual(orders[0].items[0].quantity, 100)

        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertIn("Order Confirmed!", msg)

    # -------------------------------------------------------------------------
    # Test 6: AWAITING_CONFIRMATION + "okay" -> exactly ONE order
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_6_awaiting_confirmation_plus_okay_creates_exactly_one_order(self, mock_send):
        """Active quote + 'okay' creates exactly 1 order."""
        self.client.post("/webhook", json=_make_webhook_payload("XG-502 100", self.phone))
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 0)

        mock_send.reset_mock()
        resp = self.client.post("/webhook", json=_make_webhook_payload("okay", self.phone))
        self.assertEqual(resp.status_code, 200)

        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 1, "Must create exactly 1 order with 'okay'")
        self.assertEqual(orders[0].items[0].sku, "XG-502")

        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertIn("Order Confirmed!", msg)

    # -------------------------------------------------------------------------
    # Test 7: AWAITING_CONFIRMATION + "go ahead" -> exactly ONE order
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_7_awaiting_confirmation_plus_go_ahead_creates_exactly_one_order(self, mock_send):
        """Active quote + 'go ahead' creates exactly 1 order."""
        self.client.post("/webhook", json=_make_webhook_payload("XG-503 100", self.phone))
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 0)

        mock_send.reset_mock()
        resp = self.client.post("/webhook", json=_make_webhook_payload("go ahead", self.phone))
        self.assertEqual(resp.status_code, 200)

        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 1, "Must create exactly 1 order with 'go ahead'")
        self.assertEqual(orders[0].items[0].sku, "XG-503")

    # -------------------------------------------------------------------------
    # Test 8: ORDER_CONFIRMED + "yes" -> NO second order
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_8_order_confirmed_plus_yes_creates_no_second_order(self, mock_send):
        """Once confirmed, repeated 'yes' does NOT create a second order."""
        # 1. Quote
        self.client.post("/webhook", json=_make_webhook_payload("XG-501 100", self.phone))
        # 2. First confirm
        self.client.post("/webhook", json=_make_webhook_payload("CONFIRM", self.phone))
        orders1 = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders1), 1)

        # 3. Second confirm "yes"
        mock_send.reset_mock()
        resp = self.client.post("/webhook", json=_make_webhook_payload("yes", self.phone))
        self.assertEqual(resp.status_code, 200)

        orders2 = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders2), 1, "Repeated 'yes' must not create a duplicate second order")

        args, kwargs = mock_send.call_args
        msg = kwargs.get("message") or args[1]
        self.assertIn("don't have an active quotation", msg)

    # -------------------------------------------------------------------------
    # Test 9: AWAITING_CONFIRMATION + duplicate wamid -> exactly ONE order
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_9_awaiting_confirmation_duplicate_wamid_creates_one_order(self, mock_send):
        """Duplicate webhook payload with identical wamid produces exactly 1 order."""
        self.client.post("/webhook", json=_make_webhook_payload("XG-501 100", self.phone))

        dup_wamid = f"wamid.dup_test_{uuid.uuid4().hex[:8]}"
        payload = _make_webhook_payload("CONFIRM", self.phone, msg_id=dup_wamid)

        # First delivery
        resp1 = self.client.post("/webhook", json=payload)
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 1)

        # Duplicate delivery from Meta
        resp2 = self.client.post("/webhook", json=payload)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 1, "Duplicate wamid must produce only 1 order")

    # -------------------------------------------------------------------------
    # Test 10: QUOTE_READY -> bot must transition/request confirmation before "yes" is accepted
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_10_quote_ready_explicit_transition_required_for_order(self, mock_send):
        """
        Step 1: Product selection only -> 'yes' rejected (no order)
        Step 2: Quote calculated -> 'yes' accepted -> exactly 1 order
        """
        # Step 1: User selects product SKU without quantity
        self.client.post("/webhook", json=_make_webhook_payload("XG-MP-01", self.phone))
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 0)

        # User attempts 'yes' before quote exists -> rejected
        self.client.post("/webhook", json=_make_webhook_payload("yes", self.phone))
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 0)

        # Step 2: User provides quantity -> quote generated
        self.client.post("/webhook", json=_make_webhook_payload("100", self.phone))
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 0)

        # User confirms 'yes' after quote exists -> exactly 1 order created
        self.client.post("/webhook", json=_make_webhook_payload("yes", self.phone))
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 1)

    # -------------------------------------------------------------------------
    # Realistic Production Multi-Turn Flow
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_11_exact_realistic_production_flow(self, mock_send):
        """
        Exact realistic sequence:
        1. Customer: 'XG-MP-01' -> Bot: Product details + ask quantity -> Customer: 'yes' -> NO ORDER
        2. Customer: '100' -> Bot: Quote -> Customer: 'yes' -> ONE ORDER
        """
        # Turn 1: Customer sends SKU
        mock_send.reset_mock()
        resp1 = self.client.post("/webhook", json=_make_webhook_payload("XG-MP-01", self.phone))
        self.assertEqual(resp1.status_code, 200)
        args1, kwargs1 = mock_send.call_args
        msg1 = kwargs1.get("message") or args1[1]
        self.assertIn("XG-MP-01", msg1)
        self.assertIn("quantity", msg1.lower())
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 0)

        # Turn 2: Customer sends premature 'yes'
        mock_send.reset_mock()
        resp2 = self.client.post("/webhook", json=_make_webhook_payload("yes", self.phone))
        self.assertEqual(resp2.status_code, 200)
        args2, kwargs2 = mock_send.call_args
        msg2 = kwargs2.get("message") or args2[1]
        self.assertNotIn("Order Confirmed!", msg2)
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 0, "No order after premature 'yes'")

        # Turn 3: Customer provides quantity '100'
        mock_send.reset_mock()
        resp3 = self.client.post("/webhook", json=_make_webhook_payload("100", self.phone))
        self.assertEqual(resp3.status_code, 200)
        args3, kwargs3 = mock_send.call_args
        msg3 = kwargs3.get("message") or args3[1]
        self.assertIn("Quotation for XG-MP-01", msg3)
        self.assertIn("100 units", msg3)
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 0, "Quote issued, order not yet confirmed")

        # Turn 4: Customer confirms with 'yes'
        mock_send.reset_mock()
        resp4 = self.client.post("/webhook", json=_make_webhook_payload("yes", self.phone))
        self.assertEqual(resp4.status_code, 200)
        args4, kwargs4 = mock_send.call_args
        msg4 = kwargs4.get("message") or args4[1]
        self.assertIn("Order Confirmed!", msg4)
        self.assertIn("Order ID:", msg4)
        self.assertIn("XG-MP-01", msg4)

        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 1, "Exactly 1 order after proper confirmation flow")

    # -------------------------------------------------------------------------
    # Test 12: Gemini NL intent alone cannot bypass quote requirement
    # -------------------------------------------------------------------------
    def test_12_gemini_nl_intent_alone_cannot_create_order_without_quote(self):
        """Even if Gemini classifies a message as CONFIRM_ORDER, it must fail without an active quote."""
        conv = conversation_service.get_or_create_conversation(self.phone)
        conversation_service.clear_pending_quote(conv.conversation_id)
        order_service.clear_pending_quote(self.phone)

        fake_intent = StructuredIntent(
            intent=IntentType.CONFIRM_ORDER,
            confidence=0.99,
        )
        reply = agent_router._route_intent(
            conv=conv,
            customer_phone=self.phone,
            customer_name="Test Customer",
            message_text="yes confirm please",
            intent=fake_intent,
        )
        self.assertIn("don't have an active quotation", reply)
        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 0, "Gemini intent alone must never create an order without an active quote")


if __name__ == "__main__":
    unittest.main(verbosity=2)
