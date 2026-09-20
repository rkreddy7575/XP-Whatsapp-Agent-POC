"""
test_database_isolation.py
Regression test suite proving that automated test execution strictly isolates
from the production SQLite database (backend/data/app.db) and never alters production counts.
"""

import os
import sys
import sqlite3
import unittest

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.order_models import OrderStatus
from services.order_service import (
    DEFAULT_PROD_DB_PATH,
    DEFAULT_TEST_DB_PATH,
    OrderService,
    order_service,
)
from services.conversation_service import (
    ConversationService,
    conversation_service,
)
from services.pricing_service import PriceQuoteResult


class TestDatabaseIsolation(unittest.TestCase):
    """
    Ensures that automated tests never write into backend/data/app.db.
    """

    def setUp(self):
        # Ensure production DB exists
        self.assertTrue(
            os.path.exists(DEFAULT_PROD_DB_PATH),
            f"Production DB {DEFAULT_PROD_DB_PATH} should exist for isolation verification.",
        )

        # Record production DB baseline counts
        with sqlite3.connect(DEFAULT_PROD_DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT count(*) FROM orders")
            self.baseline_prod_orders = c.fetchone()[0]

            c.execute("SELECT count(*) FROM conversations")
            self.baseline_prod_convs = c.fetchone()[0]

            c.execute("SELECT count(*) FROM conversation_messages")
            self.baseline_prod_msgs = c.fetchone()[0]

    def test_default_service_points_to_isolated_test_database_during_tests(self):
        """Verify that default singletons do NOT point to production app.db in test mode."""
        self.assertNotEqual(
            order_service.db_path,
            DEFAULT_PROD_DB_PATH,
            "order_service.db_path must never resolve to production app.db during test runs!",
        )
        self.assertNotEqual(
            conversation_service.db_path,
            DEFAULT_PROD_DB_PATH,
            "conversation_service.db_path must never resolve to production app.db during test runs!",
        )
        self.assertEqual(order_service.db_path, DEFAULT_TEST_DB_PATH)
        self.assertEqual(conversation_service.db_path, DEFAULT_TEST_DB_PATH)

    def test_order_creation_does_not_mutate_production_database(self):
        """Verify that creating orders in default services writes to test DB and leaves prod DB untouched."""
        test_phone = "919999988888"

        mock_quote = PriceQuoteResult(
            available=True,
            sku="GS-001",
            quantity=50,
            unit_price_excl_gst=445.0,
            gst_percentage=18.0,
            unit_gst=80.1,
            unit_price_incl_gst=525.1,
            total_price_excl_gst=22250.0,
            total_gst=4005.0,
            total_price_incl_gst=26255.0,
            pricing_version="2025",
        )

        # Create order using default service
        new_order = order_service.create_order_from_quote(
            customer_phone=test_phone,
            quote=mock_quote,
            customer_name="Test Isolation Buyer",
        )
        self.assertIsNotNone(new_order.order_id)

        # Verify created order is in test_app.db
        test_db_order = order_service.get_order(new_order.order_id)
        self.assertIsNotNone(test_db_order)

        # Verify conversation updates also write to test DB
        from services.conversation_models import MessageDirection
        conv = conversation_service.get_or_create_conversation(test_phone)
        conversation_service.add_message(
            conv.conversation_id,
            MessageDirection.INBOUND,
            "Testing isolation message",
        )

        # ASSERT: Production app.db MUST have the EXACT SAME counts as baseline!
        with sqlite3.connect(DEFAULT_PROD_DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT count(*) FROM orders")
            prod_orders_after = c.fetchone()[0]

            c.execute("SELECT count(*) FROM conversations")
            prod_convs_after = c.fetchone()[0]

            c.execute("SELECT count(*) FROM conversation_messages")
            prod_msgs_after = c.fetchone()[0]

            # Also verify new order ID does NOT exist in production DB
            c.execute("SELECT 1 FROM orders WHERE order_id = ?", (new_order.order_id,))
            prod_order_found = c.fetchone()

        self.assertIsNone(
            prod_order_found,
            f"Test order {new_order.order_id} was accidentally written to PRODUCTION app.db!",
        )
        self.assertEqual(
            prod_orders_after,
            self.baseline_prod_orders,
            f"Production order count changed! Baseline: {self.baseline_prod_orders}, Now: {prod_orders_after}",
        )
        self.assertEqual(
            prod_convs_after,
            self.baseline_prod_convs,
            f"Production conversation count changed! Baseline: {self.baseline_prod_convs}, Now: {prod_convs_after}",
        )
        self.assertEqual(
            prod_msgs_after,
            self.baseline_prod_msgs,
            f"Production message count changed! Baseline: {self.baseline_prod_msgs}, Now: {prod_msgs_after}",
        )


if __name__ == "__main__":
    unittest.main()
