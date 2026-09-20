import os
import sys
import unittest
from unittest.mock import MagicMock, patch

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.agent_router import AgentRouter
from services.conversation_service import ConversationService
from services.gemini_models import IntentType, StructuredIntent
from services.gemini_service import GeminiService
from services.order_service import OrderService
from services.pricing_service import PriceRule, pricing_service


class TestGeminiAgent(unittest.TestCase):
    def setUp(self):
        from services.conversation_service import conversation_service
        from services.order_service import order_service
        self.router = AgentRouter()
        self.phone = "919876543210"

        # Ensure clean conversation state for test phone
        conv = conversation_service.get_or_create_conversation(self.phone)
        conversation_service.clear_selection(conv.conversation_id)
        order_service.clear_pending_quote(self.phone)

        # Mock gemini service with mock client
        self.mock_gemini = GeminiService(api_key="mock_key_not_real", model="gemini-3.1-flash-lite")
        self.mock_client = MagicMock()
        self.mock_gemini._client = self.mock_client

        # Ensure pricing rules exist for test SKUs (including real catalogue GS-001/GS-002)
        for sku, price in [("XG-GS-501", 410.0), ("XG-GS-502", 480.0), ("GS-001", 410.0), ("GS-002", 480.0)]:
            pricing_service.add_rule(
                PriceRule(
                    sku=sku,
                    quantity_from=1,
                    quantity_to=99999,
                    base_price=price,
                    gst_percentage=18.0,
                    source_category="Gift Sets",
                )
            )

    def tearDown(self):
        from services.conversation_service import conversation_service
        from services.order_service import order_service
        conv = conversation_service.get_or_create_conversation(self.phone)
        conversation_service.clear_selection(conv.conversation_id)
        order_service.clear_pending_quote(self.phone)

    # -------------------------------------------------------------------------
    # 1. Structured Intent Parsing Unit Tests (GeminiService with Mocked Client)
    # -------------------------------------------------------------------------

    def test_parse_product_search_intent(self):
        mock_response = MagicMock()
        mock_response.text = '{"intent": "PRODUCT_SEARCH", "category": "Gift Sets", "quantity": 100, "budget_per_unit": 500.0, "query": null, "sku": null}'
        self.mock_client.models.generate_content.return_value = mock_response

        intent = self.mock_gemini.parse_intent("I need 100 gift sets under 500 each")
        self.assertEqual(intent.intent, IntentType.PRODUCT_SEARCH)
        self.assertEqual(intent.category, "Gift Sets")
        self.assertEqual(intent.quantity, 100)
        self.assertEqual(intent.budget_per_unit, 500.0)

    def test_parse_price_quote_intent(self):
        mock_response = MagicMock()
        mock_response.text = '{"intent": "PRICE_QUOTE", "category": "Writing Instruments", "quantity": 200, "sku": null}'
        self.mock_client.models.generate_content.return_value = mock_response

        intent = self.mock_gemini.parse_intent("Give me 200 metal pens")
        self.assertEqual(intent.intent, IntentType.PRICE_QUOTE)
        self.assertEqual(intent.quantity, 200)

    def test_parse_select_product_intent(self):
        mock_response = MagicMock()
        mock_response.text = '{"intent": "SELECT_PRODUCT", "selection_index": 2}'
        self.mock_client.models.generate_content.return_value = mock_response

        intent = self.mock_gemini.parse_intent("I'll take the second one")
        self.assertEqual(intent.intent, IntentType.SELECT_PRODUCT)
        self.assertEqual(intent.selection_index, 2)

    def test_parse_malformed_json_fallback(self):
        mock_response = MagicMock()
        mock_response.text = 'NOT VALID JSON RESPONSE {{{'
        self.mock_client.models.generate_content.return_value = mock_response

        # Must not crash; returns UNKNOWN
        intent = self.mock_gemini.parse_intent("gibberish")
        self.assertEqual(intent.intent, IntentType.UNKNOWN)

    def test_gemini_api_exception_fallback(self):
        self.mock_client.models.generate_content.side_effect = RuntimeError("Network timeout to Gemini API")

        # Must not crash; returns UNKNOWN
        intent = self.mock_gemini.parse_intent("Hello")
        self.assertEqual(intent.intent, IntentType.UNKNOWN)

    # -------------------------------------------------------------------------
    # 2. AgentRouter Routing & Multi-Turn Natural Language Flow Tests
    # -------------------------------------------------------------------------

    @patch("services.agent_router.gemini_service")
    def test_multi_turn_quotation_and_confirmation_flow(self, mock_gemini_svc):
        """
        Tests complete multi-turn flow:
        Turn 1: "I need 100 gift sets around 500" -> Product candidates returned, qty=100 remembered.
        Turn 2: "Second one" -> Candidate 2 resolved, instant quote generated for 100 units.
        Turn 3: "confirm" -> Order confirmed into SQLite database!
        """
        # Turn 1: Product search
        mock_gemini_svc.is_available.return_value = True
        mock_gemini_svc.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category="Gift Sets",
            quantity=100,
            budget_per_unit=600.0,
        )

        resp1 = self.router.handle_incoming_message(
            self.phone, "I need 100 gift sets around 500"
        )
        self.assertIn("matching products", resp1)
        self.assertIn("1️⃣", resp1)

        # Turn 2: Product selection ("Second one")
        mock_gemini_svc.parse_intent.return_value = StructuredIntent(
            intent=IntentType.SELECT_PRODUCT,
            selection_index=2,
        )

        resp2 = self.router.handle_incoming_message(self.phone, "Second one")
        # Should generate quotation using PricingService since quantity=100 was remembered!
        self.assertIn("Quotation for", resp2)
        self.assertIn("Subtotal:", resp2)
        self.assertIn("Grand Total:", resp2)

        # Turn 3: Confirmation ("confirm")
        # Deterministic fast-path handles confirmation directly without calling Gemini
        resp3 = self.router.handle_incoming_message(self.phone, "confirm")
        self.assertIn("Order Confirmed!", resp3)
        self.assertIn("Order ID:", resp3)

    @patch("services.agent_router.gemini_service")
    def test_product_selection_without_preexisting_quantity(self, mock_gemini_svc):
        """
        Turn 1: Search without quantity -> candidates shown.
        Turn 2: Select candidate -> asks for quantity.
        Turn 3: Quantity provided -> quotes price.
        """
        # Turn 1: Search
        mock_gemini_svc.is_available.return_value = True
        mock_gemini_svc.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category="Gift Sets",
        )
        resp1 = self.router.handle_incoming_message(self.phone, "show me gift sets")
        self.assertIn("matching products", resp1)

        # Turn 2: Select candidate 1
        mock_gemini_svc.parse_intent.return_value = StructuredIntent(
            intent=IntentType.SELECT_PRODUCT,
            selection_index=1,
        )
        resp2 = self.router.handle_incoming_message(self.phone, "first one")
        self.assertIn("Please tell me the quantity you need", resp2)

        # Turn 3: Customer replies with quantity 100
        mock_gemini_svc.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRICE_QUOTE,
            quantity=100,
        )
        resp3 = self.router.handle_incoming_message(self.phone, "100")
        self.assertIn("Quotation for", resp3)
        self.assertIn("Grand Total:", resp3)

    @patch("services.agent_router.gemini_service")
    def test_order_status_inquiry(self, mock_gemini_svc):
        # Create an order first
        from services.order_service import order_service
        quote = pricing_service.calculate_total("XG-GS-501", 100)
        order = order_service.create_order_from_quote(self.phone, quote, customer_name="Ramesh")

        mock_gemini_svc.is_available.return_value = True
        mock_gemini_svc.parse_intent.return_value = StructuredIntent(
            intent=IntentType.ORDER_STATUS,
        )

        reply = self.router.handle_incoming_message(self.phone, "where is my order?")
        self.assertIn("Order Status:", reply)
        self.assertIn(order.order_id, reply)
        self.assertIn("CONFIRMED", reply)

    @patch("services.agent_router.gemini_service")
    def test_cancel_order_intent(self, mock_gemini_svc):
        mock_gemini_svc.is_available.return_value = True
        mock_gemini_svc.parse_intent.return_value = StructuredIntent(
            intent=IntentType.CANCEL_ORDER,
        )

        reply = self.router.handle_incoming_message(self.phone, "cancel my order")
        self.assertIn("cleared", reply.lower())

    def test_deterministic_flow_unaffected(self):
        """
        Direct SKU + quantity and confirmation must execute deterministically
        without consuming Gemini API quota.
        """
        # Step 1: Direct SKU + Quantity
        quote_reply = self.router.handle_incoming_message(self.phone, "XG-GS-501 100")
        self.assertIn("Quotation for XG-GS-501", quote_reply)
        self.assertIn("₹48,380.00", quote_reply)

        # Step 2: Direct Confirmation
        confirm_reply = self.router.handle_incoming_message(self.phone, "CONFIRM")
        self.assertIn("Order Confirmed!", confirm_reply)
        self.assertIn("XG-GS-501", confirm_reply)


if __name__ == "__main__":
    unittest.main()
