import json
import os
import re
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure backend root is on sys.path
backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

from fastapi.testclient import TestClient
from main import app
from services.agent_router import agent_router
from services.catalogue_service import catalogue_service
from services.conversation_service import conversation_service
from services.gemini_models import IntentType, StructuredIntent
from services.order_models import OrderStatus
from services.order_service import order_service
from services.pricing_service import pricing_service


def make_webhook_payload(text: str, sender: str = '919999900001', wamid: str = None):
    msg_id = wamid or f'wamid.{sender}_{uuid.uuid4().hex[:12]}'
    return {
        'object': 'whatsapp_business_account',
        'entry': [
            {
                'id': '2096262090982740',
                'changes': [
                    {
                        'value': {
                            'messaging_product': 'whatsapp',
                            'metadata': {
                                'display_phone_number': '15551387741',
                                'phone_number_id': '2096262090982740',
                            },
                            'contacts': [{'profile': {'name': 'Test Client'}, 'wa_id': sender}],
                            'messages': [
                                {
                                    'from': sender,
                                    'id': msg_id,
                                    'timestamp': '1726750000',
                                    'text': {'body': text},
                                    'type': 'text',
                                }
                            ],
                        },
                        'field': 'messages',
                    }
                ],
            }
        ],
    }


def get_sent_message(mock_send) -> str:
    if not mock_send.call_args:
        return ''
    kwargs = getattr(mock_send.call_args, 'kwargs', {})
    if 'message' in kwargs:
        return kwargs['message']
    args = getattr(mock_send.call_args, 'args', ())
    if len(args) > 1:
        return args[1]
    return ''


class TestConversationUXFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        pricing_service.load_pricing_master()

    def _reset_state(self, phone: str):
        agent_router.get_pending_media_messages(phone)
        order_service.clear_pending_quote(phone)
        conv = conversation_service.get_or_create_conversation(phone)
        conversation_service.clear_selection(conv.conversation_id)
        conversation_service.clear_pending_quote(conv.conversation_id)
        if hasattr(agent_router, '_search_contexts'):
            agent_router._search_contexts.pop(phone, None)
        with order_service._get_connection() as conn:
            conn.execute(
                'DELETE FROM order_items WHERE order_id IN (SELECT order_id FROM orders WHERE customer_phone = ?)',
                (phone,),
            )
            conn.execute('DELETE FROM orders WHERE customer_phone = ?', (phone,))
            conn.execute(
                'DELETE FROM conversation_messages WHERE conversation_id = ?',
                (conv.conversation_id,),
            )
            conn.commit()

    # -------------------------------------------------------------------------
    # A. New Search: 5 ordered candidates in ONE coherent text message
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    @patch('services.agent_router.gemini_service')
    def test_a_new_search_one_coherent_text_message(self, mock_gemini, mock_send):
        phone = '919999910001'
        self._reset_state(phone)
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category='Gift Sets',
            budget_per_unit=500.0,
        )

        resp = self.client.post('/webhook', json=make_webhook_payload('gift sets under 500', phone))
        self.assertEqual(resp.status_code, 200)

        mock_send.assert_called_once()
        msg = get_sent_message(mock_send)

        # 5 ordered candidates
        self.assertIn('1️⃣', msg)
        self.assertIn('2️⃣', msg)
        self.assertIn('3️⃣', msg)
        self.assertIn('4️⃣', msg)
        self.assertIn('5️⃣', msg)
        self.assertIn('SKU:', msg)
        self.assertIn('Reply with 1', msg)
        self.assertIn('show more', msg)

        conv = conversation_service.get_or_create_conversation(phone)
        self.assertEqual(len(conv.current_product_candidates), 5)

    # -------------------------------------------------------------------------
    # B. Show More: returns second page, no welcome message, no duplicate SKUs
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    @patch('services.agent_router.gemini_service')
    def test_b_show_more_second_page_no_welcome_no_duplicates(self, mock_gemini, mock_send):
        phone = '919999910002'
        self._reset_state(phone)
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category='Gift Sets',
            budget_per_unit=500.0,
        )

        # Turn 1: Search
        self.client.post('/webhook', json=make_webhook_payload('gift sets under 500', phone))
        conv = conversation_service.get_or_create_conversation(phone)
        page1_skus = [c['sku'] for c in conv.current_product_candidates]
        self.assertEqual(len(page1_skus), 5)

        # Turn 2: 'show more'
        mock_send.reset_mock()
        mock_gemini.reset_mock()
        resp2 = self.client.post('/webhook', json=make_webhook_payload('show more', phone))
        self.assertEqual(resp2.status_code, 200)

        mock_gemini.parse_intent.assert_not_called()

        msg2 = get_sent_message(mock_send)
        self.assertNotIn('Welcome to Mudhra', msg2)
        self.assertIn('here are', msg2.lower())
        self.assertIn('1️⃣', msg2)

        conv2 = conversation_service.get_or_create_conversation(phone)
        page2_skus = [c['sku'] for c in conv2.current_product_candidates]
        self.assertTrue(len(page2_skus) > 0)
        overlap = set(page1_skus).intersection(set(page2_skus))
        self.assertEqual(overlap, set(), f'Found duplicate SKUs across pages: {overlap}')

    # -------------------------------------------------------------------------
    # C. Selection: '2' selects candidate #2 from current candidates
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    @patch('services.agent_router.gemini_service')
    def test_c_number_selection_uses_current_candidates(self, mock_gemini, mock_send):
        phone = '919999910003'
        self._reset_state(phone)
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category='Gift Sets',
        )

        self.client.post('/webhook', json=make_webhook_payload('gift sets', phone))
        conv = conversation_service.get_or_create_conversation(phone)
        expected_sku = conv.current_product_candidates[1]['sku']

        mock_send.reset_mock()
        mock_gemini.reset_mock()
        resp2 = self.client.post('/webhook', json=make_webhook_payload('2', phone))
        self.assertEqual(resp2.status_code, 200)
        mock_gemini.parse_intent.assert_not_called()

        msg2 = get_sent_message(mock_send)
        self.assertIn(expected_sku, msg2)
        self.assertIn('Great choice!', msg2)
        self.assertIn('quantity', msg2.lower())
        self.assertNotIn('Welcome to Mudhra', msg2)

        conv = conversation_service.get_or_create_conversation(phone)
        self.assertEqual(conv.selected_sku, expected_sku)

    # -------------------------------------------------------------------------
    # D. Quantity: '100' creates quote for selected product
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    @patch('services.agent_router.gemini_service')
    def test_d_quantity_flow_calculates_deterministic_quote(self, mock_gemini, mock_send):
        phone = '919999910004'
        self._reset_state(phone)
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category='Gift Sets',
        )

        self.client.post('/webhook', json=make_webhook_payload('gift sets', phone))
        self.client.post('/webhook', json=make_webhook_payload('1', phone))

        mock_send.reset_mock()
        mock_gemini.reset_mock()
        resp3 = self.client.post('/webhook', json=make_webhook_payload('100', phone))
        self.assertEqual(resp3.status_code, 200)
        mock_gemini.parse_intent.assert_not_called()

        msg3 = get_sent_message(mock_send)
        self.assertIn('Quotation for', msg3)
        self.assertIn('100 units', msg3)
        self.assertIn('Grand Total:', msg3)

        conv = conversation_service.get_or_create_conversation(phone)
        self.assertIsNotNone(conv.pending_quote)
        self.assertEqual(conv.selected_quantity, 100)

    # -------------------------------------------------------------------------
    # E. Change quantity: 'what about 200?' recalculates quote
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    @patch('services.agent_router.gemini_service')
    def test_e_change_quantity_recalculates_quote(self, mock_gemini, mock_send):
        phone = '919999910005'
        self._reset_state(phone)
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category='Gift Sets',
        )

        self.client.post('/webhook', json=make_webhook_payload('gift sets', phone))
        self.client.post('/webhook', json=make_webhook_payload('1', phone))
        self.client.post('/webhook', json=make_webhook_payload('100', phone))

        mock_send.reset_mock()
        mock_gemini.reset_mock()
        resp4 = self.client.post('/webhook', json=make_webhook_payload('what about 200?', phone))
        self.assertEqual(resp4.status_code, 200)
        mock_gemini.parse_intent.assert_not_called()

        msg4 = get_sent_message(mock_send)
        self.assertIn('200 units', msg4)
        self.assertIn('Grand Total:', msg4)

        conv = conversation_service.get_or_create_conversation(phone)
        self.assertEqual(conv.selected_quantity, 200)

    # -------------------------------------------------------------------------
    # F. Confirmation: 'confirm' creates exactly one order
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    @patch('services.agent_router.gemini_service')
    def test_f_confirmation_creates_single_order(self, mock_gemini, mock_send):
        phone = '919999910006'
        self._reset_state(phone)
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category='Gift Sets',
        )

        self.client.post('/webhook', json=make_webhook_payload('gift sets', phone))
        self.client.post('/webhook', json=make_webhook_payload('1', phone))
        self.client.post('/webhook', json=make_webhook_payload('100', phone))

        mock_send.reset_mock()
        resp_c = self.client.post('/webhook', json=make_webhook_payload('confirm', phone))
        self.assertEqual(resp_c.status_code, 200)
        msg_c = get_sent_message(mock_send)
        self.assertIn('Order Confirmed!', msg_c)
        self.assertIn('Order ID:', msg_c)

        orders = order_service.list_customer_orders(phone)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].items[0].quantity, 100)
        self.assertEqual(orders[0].status, OrderStatus.CONFIRMED)

    # -------------------------------------------------------------------------
    # G. Duplicate Confirmation: same wamid -> no duplicate order
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    @patch('services.agent_router.gemini_service')
    def test_g_duplicate_wamid_confirmation_idempotent(self, mock_gemini, mock_send):
        phone = '919999910007'
        self._reset_state(phone)
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category='Gift Sets',
        )

        self.client.post('/webhook', json=make_webhook_payload('gift sets', phone))
        self.client.post('/webhook', json=make_webhook_payload('1', phone))
        self.client.post('/webhook', json=make_webhook_payload('100', phone))

        # First delivery of confirm
        confirm_payload = make_webhook_payload('confirm', phone)
        resp1 = self.client.post('/webhook', json=confirm_payload)
        self.assertEqual(resp1.status_code, 200)

        # Duplicate delivery with exact same payload and wamid
        mock_send.reset_mock()
        resp2 = self.client.post('/webhook', json=confirm_payload)
        self.assertEqual(resp2.status_code, 200)
        mock_send.assert_not_called()

        orders = order_service.list_customer_orders(phone)
        self.assertEqual(len(orders), 1)

    # -------------------------------------------------------------------------
    # H. Another Product: 'show me another' / 'another one' -> alternatives
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    @patch('services.agent_router.gemini_service')
    def test_h_another_product_returns_alternatives(self, mock_gemini, mock_send):
        phone = '919999910008'
        self._reset_state(phone)
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category='Gift Sets',
        )

        self.client.post('/webhook', json=make_webhook_payload('gift sets', phone))
        self.client.post('/webhook', json=make_webhook_payload('1', phone))

        mock_send.reset_mock()
        mock_gemini.reset_mock()
        resp3 = self.client.post('/webhook', json=make_webhook_payload('another one', phone))
        self.assertEqual(resp3.status_code, 200)
        mock_gemini.parse_intent.assert_not_called()

        msg3 = get_sent_message(mock_send)
        self.assertNotIn('Welcome to Mudhra', msg3)
        self.assertIn('1️⃣', msg3)

    # -------------------------------------------------------------------------
    # I. Category Browsing: categories -> 1 -> products -> 2 -> 10 -> quote
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_i_category_browsing_full_flow(self, mock_send):
        phone = '919999910009'
        self._reset_state(phone)
        # 1. Ask for categories
        self.client.post('/webhook', json=make_webhook_payload('categories', phone))
        # 2. Select category 1 (Combos)
        self.client.post('/webhook', json=make_webhook_payload('1', phone))
        conv = conversation_service.get_or_create_conversation(phone)
        self.assertEqual(len(conv.current_product_candidates), 5)
        self.assertEqual(conv.current_product_candidates[0]['sku'], 'XG-501')

        # 3. Select product 2 (XG-502)
        mock_send.reset_mock()
        self.client.post('/webhook', json=make_webhook_payload('2', phone))
        msg3 = get_sent_message(mock_send)
        self.assertIn('XG-502', msg3)
        self.assertIn('quantity', msg3.lower())

        # 4. Give quantity 10
        mock_send.reset_mock()
        self.client.post('/webhook', json=make_webhook_payload('10', phone))
        msg4 = get_sent_message(mock_send)
        self.assertIn('XG-502', msg4)
        self.assertIn('10 units', msg4)
        self.assertIn('420.00', msg4)
        self.assertIn('4,956.00', msg4)

    # -------------------------------------------------------------------------
    # J. Contextual Fallback after product results: 'more' -> pagination NOT welcome
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    @patch('services.agent_router.gemini_service')
    def test_j_more_after_search_paginates_not_welcome(self, mock_gemini, mock_send):
        phone = '919999910010'
        self._reset_state(phone)
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category='Gift Sets',
        )

        self.client.post('/webhook', json=make_webhook_payload('gift sets', phone))

        # Say 'more'
        mock_send.reset_mock()
        mock_gemini.reset_mock()
        resp2 = self.client.post('/webhook', json=make_webhook_payload('more', phone))
        self.assertEqual(resp2.status_code, 200)
        mock_gemini.parse_intent.assert_not_called()

        msg2 = get_sent_message(mock_send)
        self.assertNotIn('Welcome to Mudhra', msg2)
        self.assertIn('here are', msg2.lower())
        self.assertIn('1️⃣', msg2)

    # -------------------------------------------------------------------------
    # K. Contextual Fallback after quote: 'what about 200?' -> recalculated quote
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    @patch('services.agent_router.gemini_service')
    def test_k_what_about_200_after_quote(self, mock_gemini, mock_send):
        phone = '919999910011'
        self._reset_state(phone)
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category='Gift Sets',
        )

        self.client.post('/webhook', json=make_webhook_payload('gift sets', phone))
        self.client.post('/webhook', json=make_webhook_payload('1', phone))
        self.client.post('/webhook', json=make_webhook_payload('100', phone))

        mock_send.reset_mock()
        resp = self.client.post('/webhook', json=make_webhook_payload('can you give me price for 250 instead?', phone))
        self.assertEqual(resp.status_code, 200)

        msg = get_sent_message(mock_send)
        self.assertNotIn('Welcome to Mudhra', msg)
        self.assertIn('250 units', msg)
        self.assertIn('Grand Total:', msg)

        conv = conversation_service.get_or_create_conversation(phone)
        self.assertEqual(conv.selected_quantity, 250)

    # -------------------------------------------------------------------------
    # L. Welcome regression: welcome must NOT appear during active conversation
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    @patch('services.agent_router.gemini_service')
    def test_l_welcome_never_appears_during_active_conversation(self, mock_gemini, mock_send):
        phone = '919999910012'
        self._reset_state(phone)
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category='Gift Sets',
        )

        # Start search
        self.client.post('/webhook', json=make_webhook_payload('gift sets', phone))

        # Check various follow-ups
        for phrase in ['more', 'show more', 'next', '2']:
            mock_send.reset_mock()
            self.client.post('/webhook', json=make_webhook_payload(phrase, phone))
            msg = get_sent_message(mock_send)
            self.assertNotIn('Welcome to Mudhra Branding Solutions! 🎁', msg, f'Welcome leaked on phrase {phrase}')

    # -------------------------------------------------------------------------
    # M. Message ordering: candidate numbers 1,2,3,4,5 in ONE ordered message
    # -------------------------------------------------------------------------
    @patch('main.send_text_message', new_callable=AsyncMock)
    @patch('services.agent_router.gemini_service')
    def test_m_candidate_numbers_ordered_in_single_message(self, mock_gemini, mock_send):
        phone = '919999910013'
        self._reset_state(phone)
        mock_gemini.is_available.return_value = True
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category='Gift Sets',
        )

        self.client.post('/webhook', json=make_webhook_payload('gift sets', phone))
        msg = get_sent_message(mock_send)

        # Positions must be strictly ascending: 1️⃣ < 2️⃣ < 3️⃣ < 4️⃣ < 5️⃣ < selection prompt
        pos1 = msg.find('1️⃣')
        pos2 = msg.find('2️⃣')
        pos3 = msg.find('3️⃣')
        pos4 = msg.find('4️⃣')
        pos5 = msg.find('5️⃣')
        pos_prompt = msg.find('Reply with 1')

        self.assertTrue(0 <= pos1 < pos2 < pos3 < pos4 < pos5 < pos_prompt, 'Candidates or prompt are out of order!')


if __name__ == '__main__':
    unittest.main()
