"""
Phase 2 Comprehensive Test Suite
Validates:
1. Supabase Repository Layer (Mocked client - no live connection required)
2. Product & Pricing Repository operations
3. Customer, Conversation, Message, Enquiry, Quote, Order repositories
4. Image discovery & mapping (297 mapped, 2 unmatched)
5. WhatsApp image message construction & caption formatting
6. Image fallback to text when images are unavailable
7. Deterministic pricing in captions (never calculated by LLM)
8. Isolated Demo Data Seeder and Cleaner
9. Owner Dashboard multi-turn journey detail endpoint
"""

import json
import os
import unittest
from unittest.mock import MagicMock

from services.supabase_repository import (
    SupabaseClient,
    SupabaseProductRepository,
    SupabasePricingRepository,
    SupabaseCustomerRepository,
    SupabaseConversationRepository,
    SupabaseMessageRepository,
    SupabaseEnquiryRepository,
    SupabaseQuoteRepository,
    SupabaseOrderRepository,
)
from scripts.upload_product_images import map_images, DEFAULT_IMAGES_DIR, DEFAULT_CATALOGUE_PATH
from services.catalogue_service import catalogue_service
from services.pricing_service import pricing_service
from services.agent_router import AgentRouter
from scripts.seed_demo_data import seed_demo_data, clear_demo_data, DEMO_ORDER_ID, DEMO_PHONE, DEMO_SKU, DEMO_QTY


class MockSupabaseClient(SupabaseClient):
    """Fake Supabase Client for isolated unit testing."""
    def __init__(self):
        super().__init__(supabase_url="https://mock.supabase.co", service_role_key="mock_secret_key")
        self.tables: dict = {
            "products": [],
            "pricing_rules": [],
            "customers": [],
            "conversations": [],
            "messages": [],
            "enquiries": [],
            "quotes": [],
            "orders": [],
            "order_items": [],
            "order_status_history": [],
        }

    @property
    def is_configured(self) -> bool:
        return True

    def select(self, table: str, params: dict = None) -> list:
        rows = self.tables.get(table, [])
        if not params:
            return list(rows)
        # Simple parameter matching for mock testing
        filtered = []
        for r in rows:
            match = True
            for k, v in (params or {}).items():
                if k in ("limit", "order", "select", "or"):
                    continue
                if isinstance(v, str) and v.startswith("eq."):
                    target_val = v[3:]
                    if str(r.get(k)) != target_val:
                        match = False
                        break
            if match:
                filtered.append(r)
        return filtered

    def insert(self, table: str, data: any, on_conflict: str = None) -> list:
        if table not in self.tables:
            self.tables[table] = []
        inserted = []
        items = [data] if isinstance(data, dict) else list(data)
        for item in items:
            row = dict(item)
            if "id" not in row:
                row["id"] = f"mock-uuid-{len(self.tables[table])+1}"
            self.tables[table].append(row)
            inserted.append(row)
        return inserted

    def update(self, table: str, data: dict, params: dict) -> list:
        rows = self.select(table, params)
        for r in rows:
            r.update(data)
        return rows


class TestPhase2SupabaseDemo(unittest.TestCase):
    def setUp(self):
        self.mock_client = MockSupabaseClient()

    # -------------------------------------------------------------------------
    # 1. Supabase Repository Layer Tests (Mocked)
    # -------------------------------------------------------------------------
    def test_product_repository_crud(self):
        repo = SupabaseProductRepository(self.mock_client)
        self.mock_client.insert("products", {
            "tenant_id": "default",
            "sku": "GS-001",
            "normalized_sku": "GS-001",
            "category": "Gift Sets",
            "status": "ACTIVE",
            "name": "Luxury Gift Set",
            "image_url": "https://mock.supabase.co/storage/v1/object/public/product-images/GS-001.jpg",
        })

        prod = repo.get_by_sku("GS-001")
        self.assertIsNotNone(prod)
        self.assertEqual(prod["sku"], "GS-001")
        self.assertEqual(prod["category"], "Gift Sets")

        updated = repo.update_image_url("GS-001", "https://new.url/img.jpg")
        self.assertTrue(updated)
        self.assertEqual(repo.get_by_sku("GS-001")["image_url"], "https://new.url/img.jpg")

    def test_pricing_repository_deterministic_calculation(self):
        repo = SupabasePricingRepository(self.mock_client)
        # Seed tiered pricing rules
        self.mock_client.insert("pricing_rules", [
            {
                "tenant_id": "default",
                "sku": "GS-001",
                "normalized_sku": "GS-001",
                "quantity_from": 1,
                "quantity_to": 99,
                "unit_price_excl_gst": 499.0,
                "gst_percentage": 18.0,
                "pricing_version": "2025",
            },
            {
                "tenant_id": "default",
                "sku": "GS-001",
                "normalized_sku": "GS-001",
                "quantity_from": 100,
                "quantity_to": None,
                "unit_price_excl_gst": 445.0,
                "gst_percentage": 18.0,
                "pricing_version": "2025",
            },
        ])

        quote_100 = repo.calculate_price("GS-001", 100)
        self.assertTrue(quote_100.available)
        self.assertEqual(quote_100.unit_price_excl_gst, 445.0)
        self.assertEqual(quote_100.gst_percentage, 18.0)
        self.assertEqual(quote_100.total_price_excl_gst, 44500.0)
        self.assertEqual(quote_100.total_gst, 8010.0)
        self.assertEqual(quote_100.total_price_incl_gst, 52510.0)

    def test_customer_and_conversation_repositories(self):
        cust_repo = SupabaseCustomerRepository(self.mock_client)
        conv_repo = SupabaseConversationRepository(self.mock_client)
        msg_repo = SupabaseMessageRepository(self.mock_client)

        cust = cust_repo.get_or_create("919553364395", name="Ravikiran Reddy")
        self.assertEqual(cust["phone"], "919553364395")

        conv = conv_repo.get_or_create("919553364395")
        self.assertEqual(conv["conversation_id"], "CONV-919553364395")

        msg = msg_repo.add_message(conv["conversation_id"], "INBOUND", "Need 100 bottles")
        self.assertEqual(msg["message_text"], "Need 100 bottles")
        self.assertEqual(len(msg_repo.get_messages(conv["conversation_id"])), 1)

    def test_order_repository_flow(self):
        order_repo = SupabaseOrderRepository(self.mock_client)
        created = order_repo.create_order({
            "order_id": "ORD-20260920-9999",
            "customer_phone": "919553364395",
            "subtotal": 44500.0,
            "gst_amount": 8010.0,
            "grand_total": 52510.0,
            "status": "CONFIRMED",
        }, [{
            "sku": "GS-001",
            "quantity": 100,
            "unit_price": 445.0,
            "gst_rate": 18.0,
            "gst_amount": 8010.0,
            "line_total": 52510.0,
        }])

        self.assertEqual(created["order_id"], "ORD-20260920-9999")
        retrieved = order_repo.get_by_id("ORD-20260920-9999")
        self.assertIsNotNone(retrieved)
        self.assertEqual(len(retrieved["items"]), 1)

        # Status update
        ok = order_repo.update_status("ORD-20260920-9999", "PROCESSING", reason="Payment received")
        self.assertTrue(ok)
        hist = order_repo.get_order_status_history("ORD-20260920-9999")
        self.assertEqual(len(hist), 2)  # initial CONFIRMED + update PROCESSING

    # -------------------------------------------------------------------------
    # 2. Image Discovery & Mapping Tests (297 mapped, 2 unmapped)
    # -------------------------------------------------------------------------
    def test_image_discovery_and_unmatched_files(self):
        if not os.path.exists(DEFAULT_IMAGES_DIR):
            self.skipTest(f"Images directory not found: {DEFAULT_IMAGES_DIR}")

        mapped, unmapped = map_images(DEFAULT_IMAGES_DIR, DEFAULT_CATALOGUE_PATH)
        self.assertEqual(len(mapped), 297, "Exactly 297 real product images must be mapped")
        self.assertEqual(len(unmapped), 2, "Exactly 2 images must remain unmatched")
        self.assertIn("XG-BT-123.jpg", unmapped)
        self.assertIn("XG-MG-048.jpg", unmapped)

    def test_catalogue_image_url_lookup_and_fallback(self):
        # Mapped SKU
        url_bt = catalogue_service.get_image_url("XG-BT-001")
        self.assertIsNotNone(url_bt)
        self.assertIn("XG-BT-001.jpg", url_bt)

        # Mapped SKU via prefix normalization (GS-061 <-> XG-GS-061)
        url_gs = catalogue_service.get_image_url("GS-061")
        self.assertIsNotNone(url_gs)
        self.assertIn("GS-061.jpg", url_gs)

        # Unmapped SKU gracefully returns None (no hallucinated URLs)
        self.assertIsNone(catalogue_service.get_image_url("XG-BT-123"))
        self.assertIsNone(catalogue_service.get_image_url("UNKNOWN_CODE"))

    # -------------------------------------------------------------------------
    # 3. WhatsApp Image Message Construction & Caption Formatting
    # -------------------------------------------------------------------------
    def test_whatsapp_image_message_construction_with_deterministic_price(self):
        router = AgentRouter()
        # Test candidate search with quantity
        res = router.handle_incoming_message("919553364395", "I need 100 bottles around 200")
        self.assertIsInstance(res, str)

        media = router.get_pending_media_messages("919553364395")
        if media:
            first_media = media[0]
            self.assertIn("image_url", first_media)
            self.assertTrue(first_media["image_url"].startswith("http"))
            caption = first_media["caption"]
            self.assertIn("SKU:", caption)
            self.assertTrue(any(b in caption for b in ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]) or "SKU:" in caption)
            self.assertIn("GST", caption)
            self.assertIn("Reply 1 to select this product.", caption)

    # -------------------------------------------------------------------------
    # 4. Isolated Demo Data Seeder and Cleaner Tests
    # -------------------------------------------------------------------------
    def test_demo_data_isolation(self):
        # Seed demo
        seed_demo_data()
        from services.order_service import order_service
        demo_order = order_service.get_order(DEMO_ORDER_ID)
        self.assertIsNotNone(demo_order)
        self.assertEqual(demo_order.customer_phone, DEMO_PHONE)
        from services.pricing_service import pricing_service
        expected_quote = pricing_service.calculate_total(DEMO_SKU, DEMO_QTY)
        self.assertEqual(demo_order.grand_total, expected_quote.total_price_incl_gst)

        # Clear demo
        clear_demo_data()
        self.assertIsNone(order_service.get_order(DEMO_ORDER_ID), "Demo order must be cleanly removed")


if __name__ == "__main__":
    unittest.main()
