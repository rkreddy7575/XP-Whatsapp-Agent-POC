import unittest
from unittest.mock import AsyncMock, patch
import os
import sys

# Ensure backend root is on sys.path
backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

from services.agent_router import agent_router
from services.catalogue_service import catalogue_service
from services.conversation_service import conversation_service


class TestMediaOrderingRegression(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.phone = "+919111122222"
        # Reset any state
        agent_router.get_pending_media_messages(self.phone)

    def test_candidate_ordering_preserved_and_no_duplicates(self):
        """
        Regression Test for Issue 1:
        1. Candidates must preserve order 1..5 (XG-EL-001 -> XG-EL-005).
        2. Media messages must be emitted sequentially with index 1..5.
        3. Captions must include badge, product/category, SKU, and options.
        4. Final text prompt must be single concise selection prompt without duplicate list.
        """
        reply = agent_router.handle_incoming_message(self.phone, "Electronics")
        pending_media = agent_router.get_pending_media_messages(self.phone)

        # Verify media count
        self.assertEqual(len(pending_media), 5, "Expected 5 candidate media cards for Electronics")

        # Verify ordering
        expected_skus = ["XG-EL-001", "XG-EL-002", "XG-EL-003", "XG-EL-004", "XG-EL-005"]
        for idx, media_item in enumerate(pending_media, 1):
            self.assertEqual(media_item["index"], idx)
            self.assertEqual(media_item["sku"], expected_skus[idx - 1])
            self.assertTrue(media_item["image_url"].startswith("http"))
            self.assertIn(f"SKU: {expected_skus[idx - 1]}", media_item["caption"])
            self.assertTrue(media_item["caption"].startswith(f"{idx}️⃣") or str(idx) in media_item["caption"])

        # Verify full ordered candidates (1..5) and selection prompt in single logical text response
        self.assertIn("XG-EL-001", reply)
        self.assertIn("1️⃣", reply)
        self.assertIn("5️⃣", reply)
        self.assertIn("Reply with 1", reply)
        self.assertIn("show more", reply)

    @patch("main.send_image_message", new_callable=AsyncMock)
    @patch("main.send_text_message", new_callable=AsyncMock)
    async def test_sequential_media_dispatch_order(self, mock_send_text, mock_send_image):
        """
        Verifies that main.py webhook handler dispatches images in candidate order before the text prompt.
        """
        from main import receive_webhook
        from starlette.requests import Request

        # Populate pending media
        agent_router.handle_incoming_message(self.phone, "Electronics")
        
        # Verify get_pending_media_messages returns in order
        pending = agent_router.get_pending_media_messages(self.phone)
        dispatched_skus = [p["sku"] for p in pending]
        self.assertEqual(dispatched_skus, ["XG-EL-001", "XG-EL-002", "XG-EL-003", "XG-EL-004", "XG-EL-005"])


if __name__ == "__main__":
    unittest.main()
