import os
import sys
import unittest

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.conversation_models import MessageDirection
from services.conversation_service import ConversationService


class TestConversationService(unittest.TestCase):
    def setUp(self):
        # Isolated in-memory SQLite database
        self.service = ConversationService(db_path=":memory:")
        self.phone = "919876543210"

    def test_1_get_or_create_conversation(self):
        conv = self.service.get_or_create_conversation(self.phone)
        self.assertIsNotNone(conv)
        self.assertEqual(conv.customer_phone, self.phone)
        self.assertTrue(conv.conversation_id.startswith("CONV-"))
        self.assertEqual(conv.current_product_candidates, [])
        self.assertIsNone(conv.selected_sku)

        # Calling again returns existing conversation
        conv2 = self.service.get_or_create_conversation(self.phone)
        self.assertEqual(conv.conversation_id, conv2.conversation_id)

    def test_2_add_and_retrieve_messages(self):
        conv = self.service.get_or_create_conversation(self.phone)
        m1 = self.service.add_message(conv.conversation_id, MessageDirection.INBOUND, "Hi there")
        m2 = self.service.add_message(conv.conversation_id, MessageDirection.OUTBOUND, "Hello! How can I help?")

        history = self.service.get_recent_messages(conv.conversation_id)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].message_text, "Hi there")
        self.assertEqual(history[0].direction, MessageDirection.INBOUND)
        self.assertEqual(history[1].message_text, "Hello! How can I help?")
        self.assertEqual(history[1].direction, MessageDirection.OUTBOUND)

    def test_3_candidates_storage_and_indexing(self):
        conv = self.service.get_or_create_conversation(self.phone)
        candidates = [
            {"sku": "XG-GS-501", "name": "Executive Gift Set", "source_price": 410.0},
            {"sku": "XG-GS-502", "name": "Premium Diary & Pen Combo", "source_price": 480.0},
            {"sku": "XG-GS-503", "name": "Wooden Gifting Box", "source_price": 520.0},
        ]
        self.service.set_candidates(conv.conversation_id, candidates)

        # 1-based indexing: index 1 -> XG-GS-501, index 2 -> XG-GS-502
        c1 = self.service.get_candidate_by_index(conv.conversation_id, 1)
        self.assertIsNotNone(c1)
        self.assertEqual(c1["sku"], "XG-GS-501")

        c2 = self.service.get_candidate_by_index(conv.conversation_id, 2)
        self.assertIsNotNone(c2)
        self.assertEqual(c2["sku"], "XG-GS-502")

        c3 = self.service.get_candidate_by_index(conv.conversation_id, 3)
        self.assertIsNotNone(c3)
        self.assertEqual(c3["sku"], "XG-GS-503")

        # Out-of-bounds returns None
        self.assertIsNone(self.service.get_candidate_by_index(conv.conversation_id, 4))
        self.assertIsNone(self.service.get_candidate_by_index(conv.conversation_id, 0))

    def test_4_selected_product_and_quantity(self):
        conv = self.service.get_or_create_conversation(self.phone)
        self.service.set_selected_product(conv.conversation_id, "XG-GS-501", quantity=100)

        updated = self.service.get_conversation(conv.conversation_id)
        self.assertEqual(updated.selected_sku, "XG-GS-501")
        self.assertEqual(updated.selected_quantity, 100)

        # Update quantity only
        self.service.set_selected_quantity(conv.conversation_id, 250)
        updated2 = self.service.get_conversation(conv.conversation_id)
        self.assertEqual(updated2.selected_quantity, 250)

    def test_5_pending_quote_lifecycle(self):
        conv = self.service.get_or_create_conversation(self.phone)
        quote_dict = {
            "sku": "XG-GS-501",
            "quantity": 100,
            "unit_price_excl_gst": 410.0,
            "total_price_incl_gst": 48380.0,
        }
        self.service.set_pending_quote(conv.conversation_id, quote_dict)

        fetched = self.service.get_pending_quote(conv.conversation_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["sku"], "XG-GS-501")
        self.assertEqual(fetched["total_price_incl_gst"], 48380.0)

        self.service.clear_pending_quote(conv.conversation_id)
        self.assertIsNone(self.service.get_pending_quote(conv.conversation_id))

    def test_6_clear_selection(self):
        conv = self.service.get_or_create_conversation(self.phone)
        self.service.set_candidates(conv.conversation_id, [{"sku": "XG-1"}])
        self.service.set_selected_product(conv.conversation_id, "XG-1", 50)
        self.service.set_pending_quote(conv.conversation_id, {"sku": "XG-1"})

        self.service.clear_selection(conv.conversation_id)
        cleared = self.service.get_conversation(conv.conversation_id)
        self.assertEqual(cleared.current_product_candidates, [])
        self.assertIsNone(cleared.selected_sku)
        self.assertIsNone(cleared.selected_quantity)
        self.assertIsNone(cleared.pending_quote)


if __name__ == "__main__":
    unittest.main()
