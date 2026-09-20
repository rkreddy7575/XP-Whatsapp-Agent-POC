import os
import sys
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from main import app
from services.conversation_service import ConversationService


class TestDashboardEnquiriesAPI(unittest.TestCase):
    def setUp(self):
        self.conv_service = ConversationService(db_path=":memory:")
        self.auth_token = "test-dashboard-secret-token-key-12345"
        os.environ["DASHBOARD_API_KEY"] = self.auth_token
        self.client = TestClient(app, headers={"Authorization": f"Bearer {self.auth_token}"})

    def test_get_enquiries_empty(self):
        with patch("main.conversation_service", self.conv_service):
            response = self.client.get("/api/conversations/enquiries")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), [])

    def test_get_enquiries_with_active_conversations(self):
        with patch("main.conversation_service", self.conv_service):
            # Create conversation 1 with pending quote
            conv1 = self.conv_service.get_or_create_conversation("919553364395")
            self.conv_service.set_selected_product(conv1.conversation_id, "GS-001", 100)
            self.conv_service.set_pending_quote(
                conv1.conversation_id,
                {
                    "sku": "GS-001",
                    "quantity": 100,
                    "unit_price_excl_gst": 445.0,
                    "total_price_incl_gst": 52510.0,
                },
            )

            # Create conversation 2 without quote
            conv2 = self.conv_service.get_or_create_conversation("919876543210")
            self.conv_service.set_candidates(
                conv2.conversation_id,
                [{"sku": "GS-001"}, {"sku": "GS-002"}],
            )

            response = self.client.get("/api/conversations/enquiries")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(len(data), 2)

            # Check fields
            first = data[0]
            for field in [
                "conversation_id",
                "customer_phone",
                "customer_name",
                "enquiry_text",
                "last_intent",
                "products_discussed",
                "selected_sku",
                "selected_quantity",
                "candidate_count",
                "has_pending_quote",
                "quotation_status",
                "pending_quote",
                "latest_order_id",
                "latest_order_status",
                "created_at",
                "updated_at",
            ]:
                self.assertIn(field, first)

            # Validate pending quote details in conv1
            conv1_record = next(c for c in data if c["customer_phone"] == "919553364395")
            self.assertTrue(conv1_record["has_pending_quote"])
            self.assertEqual(conv1_record["selected_sku"], "GS-001")
            self.assertEqual(conv1_record["selected_quantity"], 100)
            self.assertEqual(conv1_record["quotation_status"], "ACTIVE_QUOTE")
            self.assertEqual(conv1_record["pending_quote"]["total_price_incl_gst"], 52510.0)
            self.assertIn("GS-001", conv1_record["products_discussed"])


if __name__ == "__main__":
    unittest.main()
