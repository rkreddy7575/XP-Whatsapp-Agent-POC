import os
import sys
import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.order_models import Order, OrderItem, OrderStatus
from services.order_service import OrderService, order_service
from services.pricing_service import PriceQuoteResult, pricing_service
from main import app


class TestOrderService(unittest.TestCase):
    def setUp(self):
        # Use an isolated in-memory SQLite database for unit tests
        self.service = OrderService(db_path=":memory:")

        # Sample valid quote
        self.sample_quote = PriceQuoteResult(
            available=True,
            sku="XG-GS-501",
            quantity=100,
            unit_price_excl_gst=410.0,
            gst_percentage=18.0,
            unit_gst=73.80,
            unit_price_incl_gst=483.80,
            total_price_excl_gst=41000.0,
            total_gst=7380.0,
            total_price_incl_gst=48380.0,
            pricing_version="2025",
        )

    def test_1_create_order_from_valid_quotation(self):
        order = self.service.create_order_from_quote("919876543210", self.sample_quote, customer_name="Acme Corp")
        self.assertIsNotNone(order)
        self.assertTrue(order.order_id.startswith("ORD-"))
        self.assertEqual(order.customer_phone, "919876543210")
        self.assertEqual(order.customer_name, "Acme Corp")
        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        self.assertEqual(len(order.items), 1)
        self.assertEqual(order.subtotal, 41000.0)
        self.assertEqual(order.gst_amount, 7380.0)
        self.assertEqual(order.grand_total, 48380.0)
        self.assertEqual(order.pricing_version, "2025")
        self.assertEqual(order.inventory_status, "availability_confirmation_required")

    def test_2_order_id_generation(self):
        id1 = self.service.generate_order_id()
        self.assertTrue(id1.startswith("ORD-"))
        # Creating an order advances the counter
        self.service.create_order_from_quote("919876543210", self.sample_quote)
        id2 = self.service.generate_order_id()
        self.assertNotEqual(id1, id2)

    def test_3_price_snapshot_preservation(self):
        # Create an order with snapshotted price
        order = self.service.create_order_from_quote("919876543210", self.sample_quote)
        item = order.items[0]
        self.assertEqual(item.unit_price, 410.0)

        # Even if quotation object or underlying price table changes later, persisted order remains unchanged
        fetched = self.service.get_order(order.order_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.items[0].unit_price, 410.0)
        self.assertEqual(fetched.items[0].line_total, 48380.0)

    def test_4_gst_preservation(self):
        order = self.service.create_order_from_quote("919876543210", self.sample_quote)
        item = order.items[0]
        self.assertEqual(item.gst_rate, 18.0)
        self.assertEqual(item.gst_amount, 7380.0)
        self.assertEqual(order.gst_amount, 7380.0)

    def test_5_multiple_order_items(self):
        items = [
            OrderItem(sku="XG-GS-501", quantity=50, unit_price=410.0, gst_rate=18.0, gst_amount=3690.0, line_total=24190.0),
            OrderItem(sku="XG-BT-001", quantity=100, unit_price=130.0, gst_rate=12.0, gst_amount=1560.0, line_total=14560.0),
        ]
        order = self.service.create_order("919876543210", items)
        self.assertEqual(len(order.items), 2)
        expected_subtotal = round(410.0 * 50 + 130.0 * 100, 2)
        expected_gst = round(3690.0 + 1560.0, 2)
        expected_grand = round(expected_subtotal + expected_gst, 2)
        self.assertEqual(order.subtotal, expected_subtotal)
        self.assertEqual(order.gst_amount, expected_gst)
        self.assertEqual(order.grand_total, expected_grand)

    def test_6_customer_order_lookup(self):
        self.service.create_order_from_quote("919876543210", self.sample_quote)
        self.service.create_order_from_quote("919876543210", self.sample_quote)
        self.service.create_order_from_quote("919999999999", self.sample_quote)

        orders_customer1 = self.service.list_customer_orders("919876543210")
        self.assertEqual(len(orders_customer1), 2)

        orders_customer2 = self.service.list_customer_orders("919999999999")
        self.assertEqual(len(orders_customer2), 1)

        orders_none = self.service.list_customer_orders("910000000000")
        self.assertEqual(len(orders_none), 0)

    def test_7_status_updates(self):
        order = self.service.create_order_from_quote("919876543210", self.sample_quote)
        self.assertEqual(order.status, OrderStatus.CONFIRMED)

        updated = self.service.update_order_status(order.order_id, OrderStatus.PROCESSING)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, OrderStatus.PROCESSING)

        ready = self.service.update_order_status(order.order_id, OrderStatus.READY_FOR_DISPATCH)
        self.assertEqual(ready.status, OrderStatus.READY_FOR_DISPATCH)

    def test_8_cancellation(self):
        order = self.service.create_order_from_quote("919876543210", self.sample_quote)
        cancelled = self.service.cancel_order(order.order_id)
        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled.status, OrderStatus.CANCELLED)

        # Check DB reflects cancellation
        persisted = self.service.get_order(order.order_id)
        self.assertEqual(persisted.status, OrderStatus.CANCELLED)

    def test_9_invalid_order_creation(self):
        # 1. Unavailable quote
        bad_quote = PriceQuoteResult(available=False, sku="XG-501", quantity=100, message="Unavailable")
        with self.assertRaises(ValueError):
            self.service.create_order_from_quote("919876543210", bad_quote)

        # 2. Zero quantity quote
        zero_qty_quote = PriceQuoteResult(
            available=True, sku="XG-GS-501", quantity=0, unit_price_excl_gst=410.0, total_price_incl_gst=0.0
        )
        with self.assertRaises(ValueError):
            self.service.create_order_from_quote("919876543210", zero_qty_quote)

        # 3. Empty items list
        with self.assertRaises(ValueError):
            self.service.create_order("919876543210", [])

        # 4. Missing phone
        with self.assertRaises(ValueError):
            self.service.create_order("", [
                OrderItem(sku="XG-GS-501", quantity=10, unit_price=410.0, gst_rate=18.0, gst_amount=738.0, line_total=4838.0)
            ])

    def test_10_confirmation_detection(self):
        # Valid confirmation keywords
        self.assertTrue(OrderService.is_confirmation_intent("CONFIRM"))
        self.assertTrue(OrderService.is_confirmation_intent("confirm"))
        self.assertTrue(OrderService.is_confirmation_intent("YES"))
        self.assertTrue(OrderService.is_confirmation_intent("yes"))
        self.assertTrue(OrderService.is_confirmation_intent("CONFIRMED"))
        self.assertTrue(OrderService.is_confirmation_intent("PLACE ORDER"))
        self.assertTrue(OrderService.is_confirmation_intent("place order"))
        self.assertTrue(OrderService.is_confirmation_intent("  Confirm  "))
        self.assertTrue(OrderService.is_confirmation_intent("YES!"))

    def test_11_no_order_for_non_confirmation_messages(self):
        # Explicitly required to reject:
        self.assertFalse(OrderService.is_confirmation_intent("maybe"))
        self.assertFalse(OrderService.is_confirmation_intent("check"))
        self.assertFalse(OrderService.is_confirmation_intent("not sure"))
        self.assertFalse(OrderService.is_confirmation_intent("how much"))
        self.assertFalse(OrderService.is_confirmation_intent("change quantity"))
        self.assertFalse(OrderService.is_confirmation_intent("no"))
        self.assertFalse(OrderService.is_confirmation_intent("cancel"))
        self.assertFalse(OrderService.is_confirmation_intent("XG-GS-501 100"))
        self.assertFalse(OrderService.is_confirmation_intent(""))

    def test_12_duplicate_confirmation_protection(self):
        # Set pending quote
        self.service.set_pending_quote("919876543210", self.sample_quote)

        # First confirmation -> succeeds
        order1 = self.service.confirm_pending_order("919876543210")
        self.assertIsNotNone(order1)

        # Second confirmation immediately after -> must return None (pending quote was consumed)
        order2 = self.service.confirm_pending_order("919876543210")
        self.assertIsNone(order2)

    def test_14_list_all_orders(self):
        self.service.create_order_from_quote("919876543210", self.sample_quote, customer_name="Ramesh Sharma")
        self.service.create_order_from_quote("919123456789", self.sample_quote, customer_name="Anita Patel")
        orders = self.service.list_all_orders()
        self.assertEqual(len(orders), 2)
        # Newest first
        self.assertEqual(orders[0].customer_name, "Anita Patel")
        self.assertEqual(orders[1].customer_name, "Ramesh Sharma")

    def test_15_list_all_orders_filtered(self):
        o1 = self.service.create_order_from_quote("919876543210", self.sample_quote, customer_name="Ramesh Sharma")
        o2 = self.service.create_order_from_quote("919123456789", self.sample_quote, customer_name="Anita Patel")
        self.service.update_order_status(o2.order_id, OrderStatus.PROCESSING)

        by_status = self.service.list_all_orders(status="PROCESSING")
        self.assertEqual(len(by_status), 1)
        self.assertEqual(by_status[0].order_id, o2.order_id)

        by_search_name = self.service.list_all_orders(search="Ramesh")
        self.assertEqual(len(by_search_name), 1)
        self.assertEqual(by_search_name[0].order_id, o1.order_id)

        by_search_phone = self.service.list_all_orders(search="9123456789")
        self.assertEqual(len(by_search_phone), 1)
        self.assertEqual(by_search_phone[0].order_id, o2.order_id)


class TestWebhookOrderConfirmationIntegration(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.sender = "919876543210"
        order_service.clear_pending_quote(self.sender)

    def tearDown(self):
        order_service.clear_pending_quote(self.sender)

    def _make_payload(self, text: str):
        return {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "2096262090982740",
                "changes": [{
                    "value": {
                        "messaging_product": "whatsapp",
                        "contacts": [{"profile": {"name": "Test Customer"}, "wa_id": self.sender}],
                        "messages": [{
                            "from": self.sender,
                            "id": "wamid.test_001",
                            "timestamp": "1726750000",
                            "text": {"body": text},
                            "type": "text"
                        }]
                    },
                    "field": "messages"
                }]
            }]
        }

    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_end_to_end_quote_then_confirm_flow(self, mock_send):
        # Step 1: Customer requests quotation
        resp1 = self.client.post("/webhook", json=self._make_payload("XG-GS-501 100"))
        self.assertEqual(resp1.status_code, 200)
        args1, kwargs1 = mock_send.call_args
        msg1 = kwargs1.get("message") or args1[1]
        self.assertIn("Quotation for XG-GS-501", msg1)
        self.assertIn("₹48,380.00", msg1)

        # Step 2: Customer sends CONFIRM
        mock_send.reset_mock()
        resp2 = self.client.post("/webhook", json=self._make_payload("CONFIRM"))
        self.assertEqual(resp2.status_code, 200)
        args2, kwargs2 = mock_send.call_args
        msg2 = kwargs2.get("message") or args2[1]
        self.assertIn("Order Confirmed!", msg2)
        self.assertIn("Order ID:", msg2)
        self.assertIn("XG-GS-501", msg2)
        self.assertIn("₹48,380.00", msg2)

        # Step 3: Duplicate CONFIRM immediately rejected
        mock_send.reset_mock()
        resp3 = self.client.post("/webhook", json=self._make_payload("CONFIRM"))
        self.assertEqual(resp3.status_code, 200)
        args3, kwargs3 = mock_send.call_args
        msg3 = kwargs3.get("message") or args3[1]
        self.assertIn("don't have an active quotation pending confirmation", msg3)


if __name__ == "__main__":
    unittest.main()
