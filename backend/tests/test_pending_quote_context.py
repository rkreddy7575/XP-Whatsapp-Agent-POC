"""
Regression tests for the pending-quote conversation-state bug.

Exact real WhatsApp sequence that exposed the bug:
  "I need 100 gift sets around ₹500"  →  5 product candidates
  "1"                                   →  Quotation for GS-001
  "what next"                           →  SHOULD return contextual guidance (not generic welcome)

Test coverage:
  1. "what next" with pending quote → contextual guidance with CONFIRM
  2. "what next" → "confirm" → exactly one order created
  3. "what next" without pending quote → generic help (acceptable)
  4. "yes" with pending quote → treated as confirmation
  5. Duplicate "confirm" → must not create a second order
  6. Various phrasings: "how do I proceed", "what should I do", "next", "now what"
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure backend is on the import path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.agent_router import AgentRouter
from services.conversation_service import ConversationService
from services.order_service import OrderService
from services.pricing_service import PriceQuoteResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_CANDIDATES = [
    {"sku": "GS-001", "category": "Gift Sets", "subcategory": "Gift Set (2 in 1)"},
    {"sku": "GS-002", "category": "Gift Sets", "subcategory": "Gift Set (2 in 1)"},
    {"sku": "GS-003", "category": "Gift Sets", "subcategory": "Gift Set (2 in 1)"},
    {"sku": "GS-004", "category": "Gift Sets", "subcategory": "Gift Set (2 in 1)"},
    {"sku": "GS-005", "category": "Gift Sets", "subcategory": "Gift Set (2 in 1)"},
]

FAKE_QUOTE = PriceQuoteResult(
    sku="GS-001",
    quantity=100,
    available=True,
    unit_price_excl_gst=445.0,
    gst_percentage=18.0,
    unit_gst=80.1,
    total_gst=8010.0,
    total_price_excl_gst=44500.0,
    total_price_incl_gst=52510.0,
    message="OK",
)


def _setup_router_with_pending_quote(phone="919999000001"):
    """Sets up a fresh AgentRouter with a pending quote in conversation state."""
    conv_svc = ConversationService(db_path=":memory:")
    order_svc = OrderService(db_path=":memory:")

    router = AgentRouter()

    # Patch the module-level singletons
    patches = {
        "services.agent_router.conversation_service": conv_svc,
        "services.agent_router.order_service": order_svc,
    }

    import copy
    fresh_quote = copy.deepcopy(FAKE_QUOTE)
    fresh_quote.is_ordered = False
    fresh_quote.quote_id = None

    # Set up conversation state: product selected, pending quote
    conv = conv_svc.get_or_create_conversation(phone)
    conv_id = conv.conversation_id
    conv_svc.set_candidates(conv_id, FAKE_CANDIDATES)
    conv_svc.set_selected_product(conv_id, "GS-001", quantity=100)
    conv_svc.set_pending_quote(conv_id, fresh_quote)

    # Also set pending quote in order service
    order_svc.set_pending_quote(phone, fresh_quote)

    return router, conv_svc, order_svc, phone, patches


class TestPendingQuoteContext(unittest.TestCase):
    """Tests for contextual guidance when a pending quote exists."""

    # ------------------------------------------------------------------
    # Test 1: Exact real WhatsApp regression sequence
    # ------------------------------------------------------------------
    def test_what_next_with_pending_quote_returns_confirm_guidance(self):
        """
        Regression: "what next" after quotation MUST return contextual guidance
        containing CONFIRM, NOT the generic welcome/product list.
        """
        router, conv_svc, order_svc, phone, patches = _setup_router_with_pending_quote()

        with patch.dict("services.agent_router.__dict__", {}):
            with patch("services.agent_router.conversation_service", conv_svc), \
                 patch("services.agent_router.order_service", order_svc):
                reply = router.handle_incoming_message(phone, "what next")

        self.assertIn("CONFIRM", reply.upper())
        self.assertNotIn("Found", reply)
        self.assertNotIn("Welcome", reply)
        self.assertNotIn("GS-002", reply)  # Must not show candidate list
        self.assertIn("GS-001", reply)     # Must reference the quoted product

    # ------------------------------------------------------------------
    # Test 2: "what next" → "confirm" → exactly one order
    # ------------------------------------------------------------------
    def test_what_next_then_confirm_creates_one_order(self):
        """After 'what next', 'confirm' must create exactly one order."""
        router, conv_svc, order_svc, phone, patches = _setup_router_with_pending_quote()

        with patch("services.agent_router.conversation_service", conv_svc), \
             patch("services.agent_router.order_service", order_svc):
            # Step 1: "what next"
            reply1 = router.handle_incoming_message(phone, "what next")
            self.assertIn("CONFIRM", reply1.upper())

            # Step 2: "confirm"
            reply2 = router.handle_incoming_message(phone, "confirm")

        # Must have created exactly one order
        orders = order_svc.list_customer_orders(phone)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].items[0].sku, "GS-001")
        self.assertEqual(orders[0].items[0].quantity, 100)

        # Confirmation reply must contain order ID
        self.assertIn("ORD-", reply2)

    # ------------------------------------------------------------------
    # Test 3: "what next" WITHOUT pending quote → generic help
    # ------------------------------------------------------------------
    def test_what_next_without_pending_quote_returns_help(self):
        """
        Without a pending quote, 'what next' should fall through to
        Gemini or return generic help — NOT crash.
        """
        conv_svc = ConversationService(db_path=":memory:")
        order_svc = OrderService(db_path=":memory:")
        router = AgentRouter()

        phone = "919999000002"
        conv = conv_svc.get_or_create_conversation(phone)

        # Mock Gemini to return GENERAL_HELP
        mock_intent = MagicMock()
        mock_intent.intent = MagicMock()
        mock_intent.intent.value = "GENERAL_HELP"

        with patch("services.agent_router.conversation_service", conv_svc), \
             patch("services.agent_router.order_service", order_svc), \
             patch("services.agent_router.gemini_service") as mock_gemini:
            from services.gemini_service import IntentType
            mock_intent.intent = IntentType.GENERAL_HELP
            mock_intent.query = None
            mock_intent.sku = None
            mock_intent.quantity = None
            mock_intent.category = None
            mock_intent.budget_per_unit = None
            mock_intent.selection_index = None
            mock_intent.order_id = None
            mock_gemini.parse_intent.return_value = mock_intent

            reply = router.handle_incoming_message(phone, "what next")

        # Should contain welcome/help text, not pending quote guidance
        self.assertIn("Welcome", reply)
        self.assertNotIn("CONFIRM", reply.upper())

    # ------------------------------------------------------------------
    # Test 4: "yes" with pending quote → treated as confirmation
    # ------------------------------------------------------------------
    def test_yes_with_pending_quote_creates_order(self):
        """'yes' must be treated as confirmation when pending quote exists."""
        router, conv_svc, order_svc, phone, patches = _setup_router_with_pending_quote()

        with patch("services.agent_router.conversation_service", conv_svc), \
             patch("services.agent_router.order_service", order_svc):
            reply = router.handle_incoming_message(phone, "yes")

        orders = order_svc.list_customer_orders(phone)
        self.assertEqual(len(orders), 1)
        self.assertIn("ORD-", reply)

    # ------------------------------------------------------------------
    # Test 5: Duplicate "confirm" must NOT create a second order
    # ------------------------------------------------------------------
    def test_duplicate_confirm_does_not_create_second_order(self):
        """
        After the first 'confirm' creates an order, a second 'confirm'
        must NOT create another order.
        """
        router, conv_svc, order_svc, phone, patches = _setup_router_with_pending_quote()

        with patch("services.agent_router.conversation_service", conv_svc), \
             patch("services.agent_router.order_service", order_svc):
            # First confirm
            reply1 = router.handle_incoming_message(phone, "confirm")
            self.assertIn("ORD-", reply1)

            # Second confirm
            reply2 = router.handle_incoming_message(phone, "confirm")

        # Only one order should exist
        orders = order_svc.list_customer_orders(phone)
        self.assertEqual(len(orders), 1)

        # Second reply should say no active quotation
        self.assertIn("active quotation", reply2.lower())

    # ------------------------------------------------------------------
    # Test 6: Various phrasings of "what next"
    # ------------------------------------------------------------------
    def test_various_next_step_phrasings(self):
        """Multiple phrasings must all return pending quote guidance."""
        phrasings = [
            "what next",
            "What Next?",
            "WHAT NEXT",
            "what now",
            "next",
            "what should I do",
            "what should I do next",
            "how do I proceed",
            "how to proceed",
            "how do I order",
            "how can I order",
            "how do I place the order",
            "now what",
            "whats next",
            "next step",
            "then what",
        ]

        for phrasing in phrasings:
            router, conv_svc, order_svc, phone, patches = _setup_router_with_pending_quote(
                phone=f"91999900{phrasings.index(phrasing):04d}"
            )

            with patch("services.agent_router.conversation_service", conv_svc), \
                 patch("services.agent_router.order_service", order_svc):
                reply = router.handle_incoming_message(phone, phrasing)

            self.assertIn("CONFIRM", reply.upper(),
                          f"Phrasing '{phrasing}' did not return CONFIRM guidance. Got: {reply[:100]}")
            self.assertNotIn("Welcome", reply,
                             f"Phrasing '{phrasing}' returned generic welcome")

    # ------------------------------------------------------------------
    # Test 7: _is_next_step_question unit tests
    # ------------------------------------------------------------------
    def test_is_next_step_question_positive(self):
        """Known next-step phrases should be detected."""
        positives = [
            "what next", "what next?", "What Next", "WHAT NEXT",
            "what now", "next", "what should I do",
            "how do I proceed?", "now what", "whats next",
            "next step", "next steps", "then what",
        ]
        for phrase in positives:
            self.assertTrue(
                AgentRouter._is_next_step_question(phrase),
                f"'{phrase}' should be detected as next-step question"
            )

    def test_is_next_step_question_negative(self):
        """Non-next-step phrases should NOT be detected."""
        negatives = [
            "I need 100 gift sets",
            "confirm",
            "GS-002",
            "1",
            "categories",
            "hello",
            "change quantity",
            "order status",
            "XG-GS-501 100",
        ]
        for phrase in negatives:
            self.assertFalse(
                AgentRouter._is_next_step_question(phrase),
                f"'{phrase}' should NOT be detected as next-step question"
            )

    # ------------------------------------------------------------------
    # Test 8: _pending_quote_guidance content
    # ------------------------------------------------------------------
    def test_pending_quote_guidance_contains_required_elements(self):
        """Guidance message must contain CONFIRM, SKU, and action options."""
        from services.conversation_models import Conversation

        conv = Conversation(
            conversation_id="CONV-TEST",
            customer_phone="919999000099",
            selected_sku="GS-001",
            selected_quantity=100,
        )
        guidance = AgentRouter._pending_quote_guidance(conv)

        self.assertIn("CONFIRM", guidance)
        self.assertIn("GS-001", guidance)
        self.assertIn("100", guidance)
        self.assertIn("change quantity", guidance.lower())
        self.assertIn("change product", guidance.lower())


if __name__ == "__main__":
    unittest.main()
