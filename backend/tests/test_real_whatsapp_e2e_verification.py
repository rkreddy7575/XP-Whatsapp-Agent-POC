import uuid
import json
import os
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
from services.gemini_service import GeminiService, gemini_service
from services.order_models import OrderStatus
from services.order_service import order_service
from services.pricing_service import PriceRule, pricing_service


def make_webhook_payload(body_text: str, sender: str = "919999999991", name: str = "Corporate Buyer"):
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
                                    "id": f"wamid.{sender}_{uuid.uuid4().hex[:12]}",
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


class TestRealGeminiAndWhatsAppE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        pricing_service.load_pricing_master()

    @classmethod
    def get_auth_headers(cls):
        token = os.getenv("DASHBOARD_API_KEY", "test-dashboard-secret-token-key-12345")
        return {"Authorization": f"Bearer {token}"}

    def setUp(self):
        self.phone = "919999999991"
        self._clean_test_state(self.phone)

    def tearDown(self):
        self._clean_test_state(self.phone)

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
    # Requirement 1 & 2: Real Gemini API intent test (if GEMINI_API_KEY exists)
    # =========================================================================
    def test_real_gemini_intent_if_configured(self):
        """
        Runs one real Gemini intent test if GEMINI_API_KEY is configured in backend/.env.
        Safely skips if no key is present without failing the build.
        """
        from dotenv import load_dotenv

        env_path = os.path.join(backend_dir, ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
        else:
            load_dotenv(override=True)
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        model_name = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()

        if not api_key:
            self.skipTest(
                "GEMINI_API_KEY is not configured in backend/.env; live test safely skipped per specification."
            )

        real_gemini = GeminiService(api_key=api_key, model=model_name)
        self.assertTrue(real_gemini.is_available(), "Real Gemini service client should be initialized")

        test_phrase = "I need 100 gift sets around 500 rupees"
        parsed = real_gemini.parse_intent(test_phrase)

        if parsed.intent == IntentType.UNKNOWN and parsed.notes and any(err in parsed.notes for err in ["503", "429", "UNAVAILABLE", "high demand"]):
            self.skipTest(f"Gemini API temporarily unavailable (upstream spike): {parsed.notes}")

        self.assertIsInstance(parsed, StructuredIntent)
        self.assertIn(
            parsed.intent,
            [IntentType.PRODUCT_SEARCH, IntentType.PRICE_QUOTE],
            f"Expected search or quote intent, got {parsed.intent}",
        )
        self.assertEqual(parsed.quantity, 100, f"Expected quantity 100, got {parsed.quantity}")
        if parsed.category:
            self.assertIn(
                parsed.category.lower(),
                ["gift sets", "combos", "combos/gift sets"],
                f"Unexpected category extracted: {parsed.category}",
            )
        if parsed.budget_per_unit:
            self.assertEqual(parsed.budget_per_unit, 500.0)

    # =========================================================================
    # Requirement 3 & 4: Full Multi-Turn Natural-Language Flow via Webhook
    # =========================================================================
    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("services.agent_router.gemini_service")
    def test_whatsapp_natural_language_sales_flow_e2e(self, mock_gemini, mock_send_msg):
        """
        Verifies:
        Customer: "I need 100 gift sets around 500 rupees"
        Then Customer: "second one"
        Then Customer: "confirm"

        Verifies:
        - candidate products are stored in conversation state
        - second product resolves correctly
        - quotation uses PricingService
        - GST comes from PricingService
        - quotation displays neutral catalogue pricing note without claiming stock
        - confirmation creates exactly ONE order
        - order is persisted in SQLite
        - price snapshot is preserved
        - owner dashboard can retrieve the order
        """
        mock_gemini.is_available.return_value = True

        # ---------------------------------------------------------------------
        # Turn 1: "I need 100 gift sets around 500 rupees"
        # ---------------------------------------------------------------------
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category="Gift Sets",
            quantity=100,
            budget_per_unit=500.0,
        )

        turn1_payload = make_webhook_payload(
            "I need 100 gift sets around 500 rupees", sender=self.phone, name="Corporate Buyer"
        )
        resp1 = self.client.post("/webhook", json=turn1_payload)
        self.assertEqual(resp1.status_code, 200)
        self.assertTrue(mock_send_msg.called)

        # Check reply sent to customer
        last_call_args = mock_send_msg.call_args[1]
        msg_text_1 = last_call_args.get("message", "")
        self.assertIn("Found", msg_text_1)
        self.assertIn("matching products", msg_text_1)
        self.assertIn("1️⃣", msg_text_1)
        self.assertIn("2️⃣", msg_text_1)

        # Verify candidate products are stored in conversation state
        conv = conversation_service.get_or_create_conversation(self.phone)
        self.assertGreaterEqual(len(conv.current_product_candidates), 2)
        candidate_2 = conv.current_product_candidates[1]
        self.assertIn("sku", candidate_2)
        expected_sku = candidate_2["sku"]  # E.g. 'GS-002'

        # Verify quantity was remembered
        self.assertEqual(conv.selected_quantity, 100)

        # ---------------------------------------------------------------------
        # Turn 2: "second one"
        # ---------------------------------------------------------------------
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.SELECT_PRODUCT,
            selection_index=2,
        )

        turn2_payload = make_webhook_payload("second one", sender=self.phone)
        resp2 = self.client.post("/webhook", json=turn2_payload)
        self.assertEqual(resp2.status_code, 200)

        msg_text_2 = mock_send_msg.call_args[1].get("message", "")

        # Verify pricing comes from PricingService
        expected_quote = pricing_service.calculate_total(expected_sku, 100)
        self.assertTrue(expected_quote.available)
        self.assertIn(f"Quotation for {expected_quote.sku}", msg_text_2)
        self.assertIn("*Quantity:* 100 units", msg_text_2)
        self.assertIn("Subtotal:", msg_text_2)
        self.assertIn("Grand Total:", msg_text_2)
        self.assertIn(f"₹{expected_quote.unit_price_excl_gst:,.2f}", msg_text_2)
        self.assertIn(f"₹{expected_quote.total_price_incl_gst:,.2f}", msg_text_2)

        # Verify GST comes from PricingService
        self.assertIn(f"GST ({expected_quote.gst_percentage:.1f}%):", msg_text_2)

        # Verify stock status disclaimer
        self.assertIn("Price shown is based on the current catalogue pricing", msg_text_2)
        self.assertNotIn("Availability confirmation required", msg_text_2)

        # ---------------------------------------------------------------------
        # Turn 3: "confirm"
        # ---------------------------------------------------------------------
        turn3_payload = make_webhook_payload("confirm", sender=self.phone)
        resp3 = self.client.post("/webhook", json=turn3_payload)
        self.assertEqual(resp3.status_code, 200)

        msg_text_3 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Order Confirmed!", msg_text_3)
        self.assertIn("Order ID:", msg_text_3)

        # Extract order ID from response (e.g. ORD-20260919-0001)
        import re

        match = re.search(r"`(ORD-[0-9]{8}-[0-9]{4})`", msg_text_3)
        self.assertIsNotNone(match, "Order ID should be present in confirmation message")
        order_id = match.group(1)

        # Verify order is persisted in SQLite
        db_order = order_service.get_order(order_id)
        self.assertIsNotNone(db_order)
        self.assertEqual(db_order.customer_phone, self.phone)
        self.assertEqual(db_order.status, OrderStatus.CONFIRMED)

        # Verify price snapshot is preserved
        self.assertEqual(db_order.subtotal, expected_quote.total_price_excl_gst)
        self.assertEqual(db_order.gst_amount, expected_quote.total_gst)
        self.assertEqual(db_order.grand_total, expected_quote.total_price_incl_gst)
        self.assertEqual(len(db_order.items), 1)
        self.assertEqual(db_order.items[0].sku, expected_quote.sku)
        self.assertEqual(db_order.items[0].quantity, 100)
        self.assertEqual(db_order.items[0].unit_price, expected_quote.unit_price_excl_gst)

        # Verify Owner Dashboard can retrieve the order via REST API
        dash_resp = self.client.get(f"/api/orders/{order_id}", headers=self.get_auth_headers())
        self.assertEqual(dash_resp.status_code, 200)
        order_data = dash_resp.json()
        self.assertEqual(order_data["order_id"], order_id)
        self.assertEqual(order_data["status"], "CONFIRMED")
        self.assertEqual(order_data["grand_total"], expected_quote.total_price_incl_gst)
        self.assertEqual(order_data["subtotal"], expected_quote.total_price_excl_gst)
        self.assertEqual(order_data["gst_amount"], expected_quote.total_gst)

        # Verify order appears in the full order listing
        list_resp = self.client.get("/api/orders", headers=self.get_auth_headers())
        self.assertEqual(list_resp.status_code, 200)
        all_orders = list_resp.json()
        order_ids = [o["order_id"] for o in all_orders]
        self.assertIn(order_id, order_ids)

    # =========================================================================
    # Requirement 6: Failure Cases A, B, C, D, E
    # =========================================================================

    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("services.agent_router.gemini_service")
    def test_failure_case_a_vague_clarification(self, mock_gemini, mock_send_msg):
        """
        Failure Case A:
        Customer: "I need something"
        Expected: Helpful clarification.
        """
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            query="something",
        )

        payload = make_webhook_payload("I need something", sender="919999999992")
        resp = self.client.post("/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)

        msg = mock_send_msg.call_args[1].get("message", "")
        self.assertTrue(
            "category" in msg.lower() or "explore" in msg.lower() or "help" in msg.lower(),
            f"Expected helpful clarification, got: {msg}",
        )

    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("services.agent_router.gemini_service")
    def test_failure_case_b_quantity_without_product(self, mock_gemini, mock_send_msg):
        """
        Failure Case B:
        Customer: "Give me 100"
        Expected: Ask what product/category they want.
        """
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRICE_QUOTE,
            quantity=100,
        )

        payload = make_webhook_payload("Give me 100", sender="919999999993")
        resp = self.client.post("/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)

        msg = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("100 units", msg)
        self.assertTrue(
            "product code or category" in msg.lower() or "which product" in msg.lower(),
            f"Expected ask for product/category, got: {msg}",
        )

    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("services.agent_router.gemini_service")
    def test_failure_case_c_selection_without_candidates(self, mock_gemini, mock_send_msg):
        """
        Failure Case C:
        Customer: "second one" when there are no previous candidates.
        Expected: Ask customer to select from displayed products / search first.
        """
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.SELECT_PRODUCT,
            selection_index=2,
        )

        test_phone = "919999999994"
        self._clean_test_state(test_phone)

        payload = make_webhook_payload("second one", sender=test_phone)
        resp = self.client.post("/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)

        msg = mock_send_msg.call_args[1].get("message", "")
        self.assertTrue(
            "no active products" in msg.lower() or "search for products first" in msg.lower(),
            f"Expected prompt to search first, got: {msg}",
        )

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_failure_case_d_confirm_without_pending_quote(self, mock_send_msg):
        """
        Failure Case D:
        Customer: "confirm" when there is no pending quote.
        Expected: No order created.
        """
        test_phone = "919999999995"
        self._clean_test_state(test_phone)

        # Ensure no orders exist initially for this phone
        initial_orders = order_service.list_customer_orders(test_phone)
        self.assertEqual(len(initial_orders), 0)

        payload = make_webhook_payload("confirm", sender=test_phone)
        resp = self.client.post("/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)

        msg = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("don't have an active quotation pending confirmation", msg)

        # Verify NO order was created
        final_orders = order_service.list_customer_orders(test_phone)
        self.assertEqual(len(final_orders), 0, "No order should be created without a pending quote")

    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("services.agent_router.gemini_service")
    def test_failure_case_e_gemini_unavailable_fallback(self, mock_gemini, mock_send_msg):
        """
        Failure Case E:
        Gemini API unavailable.
        Expected: Existing deterministic fallback message.
        """
        mock_gemini.is_available.return_value = False
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.UNKNOWN,
            notes="Gemini offline",
        )

        payload = make_webhook_payload("xyz unknown query", sender="919999999996")
        resp = self.client.post("/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)

        msg = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("No products found matching \"xyz unknown query\"", msg)
        self.assertIn("Try a category like *Mugs*", msg)

    # =========================================================================
    # Requirement 7: Existing Deterministic Path
    # =========================================================================
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_deterministic_path_continues_working(self, mock_send_msg):
        """
        Customer: XG-GS-501 100
        Then: CONFIRM
        MUST continue working exactly as before.
        """
        test_phone = "919999999997"
        self._clean_test_state(test_phone)

        # Step 1: Quote
        quote_payload = make_webhook_payload("XG-GS-501 100", sender=test_phone)
        resp1 = self.client.post("/webhook", json=quote_payload)
        self.assertEqual(resp1.status_code, 200)

        msg1 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Quotation for XG-GS-501", msg1)
        self.assertIn("₹48,380.00", msg1)
        self.assertIn("Price shown is based on the current catalogue pricing", msg1)
        self.assertNotIn("Availability confirmation required", msg1)

        # Step 2: Confirm
        confirm_payload = make_webhook_payload("CONFIRM", sender=test_phone)
        resp2 = self.client.post("/webhook", json=confirm_payload)
        self.assertEqual(resp2.status_code, 200)

        msg2 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Order Confirmed!", msg2)
        self.assertIn("Grand Total:", msg2)
        self.assertIn("₹48,380.00", msg2)

    # =========================================================================
    # Requirement 8: Conversation History Persistence
    # =========================================================================
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_conversation_history_persistence(self, mock_send_msg):
        """
        Verifies inbound and outbound messages are persisted in SQLite conversation_messages.
        """
        test_phone = "919999999998"
        self._clean_test_state(test_phone)

        payload = make_webhook_payload("XG-GS-501 100", sender=test_phone)
        resp = self.client.post("/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)

        conv = conversation_service.get_or_create_conversation(test_phone)
        messages = conversation_service.get_recent_messages(conv.conversation_id, limit=10)

        # Should contain at least 1 INBOUND and 1 OUTBOUND message
        self.assertGreaterEqual(len(messages), 2)
        directions = [m.direction for m in messages]
        self.assertIn(MessageDirection.INBOUND, directions)
        self.assertIn(MessageDirection.OUTBOUND, directions)

        inbound_msg = [m for m in messages if m.direction == MessageDirection.INBOUND][0]
        self.assertEqual(inbound_msg.message_text, "XG-GS-501 100")

    # =========================================================================
    # Requirement 9: Duplicate Confirmation Protection
    # =========================================================================
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_duplicate_confirmation_creates_only_one_order(self, mock_send_msg):
        """
        Send CONFIRM twice.
        Expected: Only ONE order is created.
        """
        test_phone = "919999999999"
        self._clean_test_state(test_phone)

        # Step 1: Quote
        self.client.post("/webhook", json=make_webhook_payload("XG-GS-501 100", sender=test_phone))

        # Step 2: First CONFIRM
        resp_confirm_1 = self.client.post(
            "/webhook", json=make_webhook_payload("CONFIRM", sender=test_phone)
        )
        self.assertEqual(resp_confirm_1.status_code, 200)
        msg1 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Order Confirmed!", msg1)

        orders_after_first = order_service.list_customer_orders(test_phone)
        self.assertEqual(len(orders_after_first), 1, "Exactly one order should exist after first confirmation")

        # Step 3: Second CONFIRM (Duplicate)
        resp_confirm_2 = self.client.post(
            "/webhook", json=make_webhook_payload("CONFIRM", sender=test_phone)
        )
        self.assertEqual(resp_confirm_2.status_code, 200)
        msg2 = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("don't have an active quotation pending confirmation", msg2)

        orders_after_second = order_service.list_customer_orders(test_phone)
        self.assertEqual(
            len(orders_after_second), 1, "Still exactly one order should exist after duplicate confirmation"
        )


if __name__ == "__main__":
    unittest.main()
