import os
import unittest
import uuid
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from main import app
from services.agent_router import agent_router
from services.conversation_service import conversation_service
from services.order_service import order_service
from services.pricing_service import pricing_service, PriceQuoteResult, PriceRule
from services.catalogue_service import catalogue_service


def _make_webhook_payload(body_text: str, sender: str, msg_id: str = None) -> dict:
    wamid = msg_id or f"wamid.test_{uuid.uuid4().hex[:12]}"
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
                                    "profile": {"name": "Test Customer"},
                                    "wa_id": sender,
                                }
                            ],
                            "messages": [
                                {
                                    "from": sender,
                                    "id": wamid,
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


def _fresh_phone() -> str:
    return f"91{uuid.uuid4().int % 10000000000:010d}"


class TestQuoteStateIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pricing_service.load_pricing_master()
        # Add a pricing rule for K-001 so Test D can quote and order K-001
        pricing_service.add_rule(
            PriceRule(
                sku="K-001",
                quantity_from=1,
                quantity_to=None,
                base_price=45.0,
                gst_percentage=18.0,
                source="Keychains Price List",
                source_file="Keychains.xlsx",
                source_sheet="Sheet1",
                source_row=1,
                source_category="Keychains",
                pricing_version="2026",
            )
        )

    def setUp(self):
        self.client = TestClient(app)
        self.phone = _fresh_phone()
        self._clean_phone_state(self.phone)

    def tearDown(self):
        self._clean_phone_state(self.phone)

    def _clean_phone_state(self, phone: str):
        conv = conversation_service.get_or_create_conversation(phone)
        conversation_service.clear_selection(conv.conversation_id)
        conversation_service.clear_pending_quote(conv.conversation_id)
        conversation_service.set_candidates(conv.conversation_id, [])
        order_service.clear_pending_quote(phone)
        with order_service._get_connection() as conn:
            conn.execute(
                "DELETE FROM order_items WHERE order_id IN (SELECT order_id FROM orders WHERE customer_phone = ?)",
                (phone,),
            )
            conn.execute("DELETE FROM orders WHERE customer_phone = ?", (phone,))
            conn.commit()

    def _get_orders_for_phone(self, phone: str):
        return order_service.list_customer_orders(phone)

    # -------------------------------------------------------------------------
    # Test A:
    # 1. Create quote XG-502 x 50
    # 2. Enter/complete a new Keychains category browsing flow
    # 3. Ask "Show me images"
    # 4. Verify XG-502 is not referenced
    # 5. Verify no quote confirmation text is shown
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("main.send_image_message", new_callable=AsyncMock)
    def test_a_quote_then_category_browse_then_images(self, mock_image, mock_send):
        # 1. Quote XG-502 x 50
        resp1 = self.client.post("/webhook", json=_make_webhook_payload("XG-502 50", self.phone))
        self.assertEqual(resp1.status_code, 200)
        conv = conversation_service.get_or_create_conversation(self.phone)
        self.assertIsNotNone(conv.pending_quote)
        self.assertTrue(conv.awaiting_confirmation)

        # 2. Category browsing: "Categories" -> "5" (Keychains)
        resp2 = self.client.post("/webhook", json=_make_webhook_payload("Categories", self.phone))
        self.assertEqual(resp2.status_code, 200)

        resp3 = self.client.post("/webhook", json=_make_webhook_payload("5", self.phone))
        self.assertEqual(resp3.status_code, 200)

        # Verify active quote is invalidated
        conv = conversation_service.get_or_create_conversation(self.phone)
        self.assertIsNone(conv.pending_quote)
        self.assertFalse(conv.awaiting_confirmation)
        self.assertIsNone(order_service.get_pending_quote(self.phone))
        self.assertTrue(len(conv.current_product_candidates) > 0)

        # 3. Ask "Show me images"
        mock_send.reset_mock()
        mock_image.reset_mock()
        resp4 = self.client.post("/webhook", json=_make_webhook_payload("Show me images", self.phone))
        self.assertEqual(resp4.status_code, 200)

        # Collect all sent messages
        sent_texts = [call.kwargs.get("message", call.args[1] if len(call.args) > 1 else "") for call in mock_send.call_args_list]
        combined_text = " ".join(sent_texts)

        # 4. Verify XG-502 is NOT referenced
        self.assertNotIn("XG-502", combined_text)
        # 5. Verify no quote confirmation text is shown
        self.assertNotIn("Reply CONFIRM to place this order", combined_text)
        self.assertNotIn("CONFIRM to place this order", combined_text)

    # -------------------------------------------------------------------------
    # Test B:
    # 1. Create quote XG-502 x 50
    # 2. Start Keychains browsing
    # 3. Send "Confirm"
    # 4. Verify NO order is created
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_b_quote_then_category_browse_then_confirm_creates_no_order(self, mock_send):
        # 1. Quote XG-502 x 50
        self.client.post("/webhook", json=_make_webhook_payload("XG-502 50", self.phone))
        conv = conversation_service.get_or_create_conversation(self.phone)
        self.assertIsNotNone(conv.pending_quote)
        self.assertTrue(conv.awaiting_confirmation)

        # 2. Start Keychains browsing (send "Categories" then "5" or "Browse Keychains")
        self.client.post("/webhook", json=_make_webhook_payload("Categories", self.phone))
        self.client.post("/webhook", json=_make_webhook_payload("5", self.phone))

        # 3. Send "Confirm"
        mock_send.reset_mock()
        resp = self.client.post("/webhook", json=_make_webhook_payload("Confirm", self.phone))
        self.assertEqual(resp.status_code, 200)

        # 4. Verify NO order is created
        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 0)

        sent_msg = mock_send.call_args.kwargs.get("message", mock_send.call_args.args[1] if len(mock_send.call_args.args) > 1 else "")
        self.assertIn("don't have an active quotation", sent_msg)
        self.assertNotIn("Order Confirmed!", sent_msg)

    # -------------------------------------------------------------------------
    # Test C:
    # 1. Browse Keychains
    # 2. Send "Confirm"
    # 3. Verify NO order is created
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_c_browse_keychains_then_confirm_creates_no_order(self, mock_send):
        # 1. Browse Keychains
        self.client.post("/webhook", json=_make_webhook_payload("Browse Keychains", self.phone))

        # 2. Send "Confirm"
        mock_send.reset_mock()
        resp = self.client.post("/webhook", json=_make_webhook_payload("Confirm", self.phone))
        self.assertEqual(resp.status_code, 200)

        # 3. Verify NO order is created
        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 0)
        sent_msg = mock_send.call_args.kwargs.get("message", mock_send.call_args.args[1] if len(mock_send.call_args.args) > 1 else "")
        self.assertIn("don't have an active quotation", sent_msg)

    # -------------------------------------------------------------------------
    # Test D:
    # 1. Browse Keychains
    # 2. Select K-001
    # 3. Provide quantity
    # 4. Receive quote
    # 5. Send Confirm
    # 6. Verify only K-001 order is created
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_d_browse_select_qty_quote_confirm(self, mock_send):
        # 1. Browse Keychains
        self.client.post("/webhook", json=_make_webhook_payload("Browse Keychains", self.phone))

        # 2. Select K-001
        self.client.post("/webhook", json=_make_webhook_payload("K-001", self.phone))

        # 3. Provide quantity 50
        self.client.post("/webhook", json=_make_webhook_payload("50", self.phone))

        # 4. Verify quote is active for K-001
        conv = conversation_service.get_or_create_conversation(self.phone)
        self.assertIsNotNone(conv.pending_quote)
        self.assertEqual(conv.pending_quote.get("sku"), "K-001")
        self.assertTrue(conv.awaiting_confirmation)

        # 5. Send Confirm
        mock_send.reset_mock()
        resp = self.client.post("/webhook", json=_make_webhook_payload("Confirm", self.phone))
        self.assertEqual(resp.status_code, 200)

        # 6. Verify only K-001 order is created
        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].items[0].sku, "K-001")
        self.assertEqual(orders[0].items[0].quantity, 50)

    # -------------------------------------------------------------------------
    # Test E:
    # 1. Quote XG-502 x 50
    # 2. Ask image
    # 3. Image request must not alter quote
    # 4. Confirm immediately
    # 5. Verify XG-502 x 50 can still be confirmed ONLY because the valid quote remains explicitly active
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("main.send_image_message", new_callable=AsyncMock)
    def test_e_quote_image_confirm_flow(self, mock_image, mock_send):
        # 1. Quote XG-502 x 50
        self.client.post("/webhook", json=_make_webhook_payload("XG-502 50", self.phone))
        conv = conversation_service.get_or_create_conversation(self.phone)
        self.assertIsNotNone(conv.pending_quote)
        quote_id_before = conv.active_quote_id
        self.assertIsNotNone(quote_id_before)

        # 2. Ask image
        resp_img = self.client.post("/webhook", json=_make_webhook_payload("Show me image", self.phone))
        self.assertEqual(resp_img.status_code, 200)

        # 3. Image request must NOT alter quote or clear awaiting confirmation
        conv_after_img = conversation_service.get_or_create_conversation(self.phone)
        self.assertIsNotNone(conv_after_img.pending_quote)
        self.assertEqual(conv_after_img.active_quote_id, quote_id_before)
        self.assertTrue(conv_after_img.awaiting_confirmation)

        # 4. Confirm immediately
        resp_conf = self.client.post("/webhook", json=_make_webhook_payload("Confirm", self.phone))
        self.assertEqual(resp_conf.status_code, 200)

        # 5. Verify order is created for XG-502 x 50
        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].items[0].sku, "XG-502")
        self.assertEqual(orders[0].items[0].quantity, 50)

    # -------------------------------------------------------------------------
    # Test F:
    # Two different customers/phone numbers must never share:
    # - selected product
    # - candidates
    # - quote
    # - confirmation state
    # - image target
    # -------------------------------------------------------------------------
    @patch("main.send_text_message", new_callable=AsyncMock)
    def test_f_customer_phone_state_isolation(self, mock_send):
        phone_a = _fresh_phone()
        phone_b = _fresh_phone()
        self._clean_phone_state(phone_a)
        self._clean_phone_state(phone_b)

        try:
            # Customer A quotes XG-502 x 50
            self.client.post("/webhook", json=_make_webhook_payload("XG-502 50", phone_a))
            # Customer B browses Keychains
            self.client.post("/webhook", json=_make_webhook_payload("Browse Keychains", phone_b))

            conv_a = conversation_service.get_or_create_conversation(phone_a)
            conv_b = conversation_service.get_or_create_conversation(phone_b)

            # Customer A has quote, awaiting confirmation, selected_sku
            self.assertIsNotNone(conv_a.pending_quote)
            self.assertEqual(conv_a.pending_quote.get("sku"), "XG-502")
            self.assertTrue(conv_a.awaiting_confirmation)

            # Customer B has NO quote, NOT awaiting confirmation, candidates are Keychains
            self.assertIsNone(conv_b.pending_quote)
            self.assertFalse(conv_b.awaiting_confirmation)
            self.assertTrue(any(c.get("sku", "").startswith("K-") for c in conv_b.current_product_candidates))

            # Customer B says "Confirm" -> must NOT confirm Customer A's quote!
            self.client.post("/webhook", json=_make_webhook_payload("Confirm", phone_b))
            self.assertEqual(len(self._get_orders_for_phone(phone_b)), 0)
            self.assertEqual(len(self._get_orders_for_phone(phone_a)), 0)

            # Order creation for Customer B directly with None active_quote returns None
            self.assertIsNone(order_service.get_pending_quote(phone_b))

        finally:
            self._clean_phone_state(phone_a)
            self._clean_phone_state(phone_b)

    # -------------------------------------------------------------------------
    # Test G / Safety Rule:
    # order creation cannot happen when active_quote == None
    # -------------------------------------------------------------------------
    def test_g_order_creation_blocked_when_active_quote_is_none(self):
        # Direct service call with None quote raises ValueError
        with self.assertRaises(ValueError):
            order_service.create_order_from_quote(
                customer_phone=self.phone,
                quote=None,
            )
        orders = self._get_orders_for_phone(self.phone)
        self.assertEqual(len(orders), 0)

        # Call with already-ordered quote raises ValueError
        mock_quote = PriceQuoteResult(
            available=True,
            sku="XG-502",
            quantity=50,
            unit_price_excl_gst=420.0,
            unit_price_incl_gst=495.6,
            total_price_excl_gst=21000.0,
            total_gst=3780.0,
            total_price_incl_gst=24780.0,
            is_ordered=True,
            quote_id="Q-TESTALREADYORDERED",
            customer_phone=self.phone,
        )
        with self.assertRaises(ValueError):
            order_service.create_order_from_quote(
                customer_phone=self.phone,
                quote=mock_quote,
            )
        self.assertEqual(len(self._get_orders_for_phone(self.phone)), 0)
