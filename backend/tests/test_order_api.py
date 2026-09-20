import os
import sys
import unittest
from fastapi.testclient import TestClient

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import main
from main import app
from services.order_models import OrderItem, OrderStatus
from services.order_service import OrderService


class TestOrderAPI(unittest.TestCase):
    def setUp(self):
        # Create an isolated in-memory SQLite database
        self.test_service = OrderService(db_path=":memory:")
        self.orig_service = main.order_service
        main.order_service = self.test_service
        self.auth_token = "test-dashboard-secret-token-key-12345"
        os.environ["DASHBOARD_API_KEY"] = self.auth_token
        self.client = TestClient(app, headers={"Authorization": f"Bearer {self.auth_token}"})
        self.unauth_client = TestClient(app)

        # Populate sample orders
        self.order1 = self.test_service.create_order(
            customer_phone="919876543210",
            customer_name="Ramesh Sharma",
            items=[
                OrderItem(
                    sku="XG-GS-501",
                    quantity=100,
                    unit_price=410.0,
                    gst_rate=18.0,
                    gst_amount=7380.0,
                    line_total=48380.0,
                )
            ],
        )

        self.order2 = self.test_service.create_order(
            customer_phone="919123456789",
            customer_name="Anita Patel",
            items=[
                OrderItem(
                    sku="BOT-SS-001",
                    quantity=50,
                    unit_price=200.0,
                    gst_rate=12.0,
                    gst_amount=1200.0,
                    line_total=11200.0,
                )
            ],
        )
        self.test_service.update_order_status(self.order2.order_id, OrderStatus.PROCESSING)

        self.order3 = self.test_service.create_order(
            customer_phone="919999999999",
            customer_name="Suresh Kumar",
            items=[
                OrderItem(
                    sku="PEN-MT-005",
                    quantity=500,
                    unit_price=25.0,
                    gst_rate=18.0,
                    gst_amount=2250.0,
                    line_total=14750.0,
                )
            ],
        )
        self.test_service.update_order_status(self.order3.order_id, OrderStatus.DELIVERED)

    def tearDown(self):
        main.order_service = self.orig_service

    def test_unauthenticated_api_access_returns_401(self):
        """Unauthenticated requests must receive 401 Unauthorized."""
        response = self.unauth_client.get("/api/orders")
        self.assertEqual(response.status_code, 401)
        response = self.unauth_client.get(f"/api/orders/{self.order1.order_id}")
        self.assertEqual(response.status_code, 401)
        response = self.unauth_client.patch(
            f"/api/orders/{self.order1.order_id}/status",
            json={"status": "CONFIRMED"},
        )
        self.assertEqual(response.status_code, 401)

    def test_get_all_orders(self):
        response = self.client.get("/api/orders")
        self.assertEqual(response.status_code, 200)
        orders = response.json()
        self.assertEqual(len(orders), 3)
        
        # Verify required fields
        first = orders[0]
        for field in [
            "order_id",
            "customer_name",
            "customer_phone",
            "status",
            "subtotal",
            "gst_amount",
            "grand_total",
            "currency",
            "inventory_status",
            "created_at",
            "updated_at",
            "items",
        ]:
            self.assertIn(field, first)
        
        # Verify item structure
        item = first["items"][0]
        for field in ["sku", "quantity", "unit_price", "gst_rate", "gst_amount", "line_total"]:
            self.assertIn(field, item)

    def test_get_single_order_success(self):
        response = self.client.get(f"/api/orders/{self.order1.order_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["order_id"], self.order1.order_id)
        self.assertEqual(data["customer_name"], "Ramesh Sharma")
        self.assertEqual(data["customer_phone"], "919876543210")
        self.assertEqual(data["grand_total"], 48380.0)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["sku"], "XG-GS-501")

    def test_get_single_order_not_found(self):
        response = self.client.get("/api/orders/ORD-NONEXISTENT-9999")
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["detail"].lower())

    def test_search_orders_by_order_id(self):
        response = self.client.get(f"/api/orders?search={self.order1.order_id}")
        self.assertEqual(response.status_code, 200)
        results = response.json()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["order_id"], self.order1.order_id)

    def test_search_orders_by_phone(self):
        response = self.client.get("/api/orders?search=9123456789")
        self.assertEqual(response.status_code, 200)
        results = response.json()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["customer_name"], "Anita Patel")

    def test_search_orders_by_customer_name(self):
        response = self.client.get("/api/orders?search=Ramesh")
        self.assertEqual(response.status_code, 200)
        results = response.json()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["customer_name"], "Ramesh Sharma")

    def test_filter_orders_by_status(self):
        response = self.client.get("/api/orders?status=PROCESSING")
        self.assertEqual(response.status_code, 200)
        results = response.json()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["order_id"], self.order2.order_id)
        self.assertEqual(results[0]["status"], "PROCESSING")

    def test_valid_status_update(self):
        response = self.client.patch(
            f"/api/orders/{self.order1.order_id}/status",
            json={"status": "PROCESSING"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "PROCESSING")

        # Verify persisted via GET
        get_res = self.client.get(f"/api/orders/{self.order1.order_id}")
        self.assertEqual(get_res.json()["status"], "PROCESSING")

    def test_all_valid_statuses_accepted(self):
        valid_statuses = [
            "PENDING_CONFIRMATION",
            "CONFIRMED",
            "PROCESSING",
            "READY_FOR_DISPATCH",
            "DISPATCHED",
            "DELIVERED",
            "CANCELLED",
        ]
        for st in valid_statuses:
            res = self.client.patch(
                f"/api/orders/{self.order1.order_id}/status",
                json={"status": st},
            )
            self.assertEqual(res.status_code, 200, f"Status {st} should be accepted")
            self.assertEqual(res.json()["status"], st)

    def test_invalid_status_rejected_with_400(self):
        response = self.client.patch(
            f"/api/orders/{self.order1.order_id}/status",
            json={"status": "SHIPPED_YESTERDAY"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid status", response.json()["detail"])

    def test_status_update_unknown_order_returns_404(self):
        response = self.client.patch(
            "/api/orders/ORD-DOESNOTEXIST/status",
            json={"status": "PROCESSING"},
        )
        self.assertEqual(response.status_code, 404)

    def test_order_immutability_after_status_update(self):
        """
        Critical business rule: Updating order status MUST NOT modify
        unit prices, GST rate, subtotal, GST amount, grand total, or order items.
        """
        initial = self.client.get(f"/api/orders/{self.order1.order_id}").json()

        # Update status through several states
        self.client.patch(
            f"/api/orders/{self.order1.order_id}/status",
            json={"status": "PROCESSING"},
        )
        self.client.patch(
            f"/api/orders/{self.order1.order_id}/status",
            json={"status": "READY_FOR_DISPATCH"},
        )

        after = self.client.get(f"/api/orders/{self.order1.order_id}").json()

        # Status must have changed
        self.assertEqual(after["status"], "READY_FOR_DISPATCH")

        # Snapshot data must be strictly identical
        self.assertEqual(after["subtotal"], initial["subtotal"])
        self.assertEqual(after["gst_amount"], initial["gst_amount"])
        self.assertEqual(after["grand_total"], initial["grand_total"])
        self.assertEqual(after["currency"], initial["currency"])
        self.assertEqual(after["pricing_version"], initial["pricing_version"])
        self.assertEqual(len(after["items"]), len(initial["items"]))

        for orig_item, after_item in zip(initial["items"], after["items"]):
            self.assertEqual(orig_item["sku"], after_item["sku"])
            self.assertEqual(orig_item["quantity"], after_item["quantity"])
            self.assertEqual(orig_item["unit_price"], after_item["unit_price"])
            self.assertEqual(orig_item["gst_rate"], after_item["gst_rate"])
            self.assertEqual(orig_item["gst_amount"], after_item["gst_amount"])
            self.assertEqual(orig_item["line_total"], after_item["line_total"])


if __name__ == "__main__":
    unittest.main()
