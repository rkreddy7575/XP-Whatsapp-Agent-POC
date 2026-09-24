from unittest.mock import AsyncMock, patch
import unittest
import uuid
from fastapi.testclient import TestClient

from main import app
from services.catalogue_service import extract_sku_and_quantity_from_inquiry
from services.conversation_service import conversation_service
from services.order_service import order_service
from services.pricing_service import pricing_service


def _make_webhook_payload(text: str, phone: str = '919876543210') -> dict:
    return {
        'object': 'whatsapp_business_account',
        'entry': [
            {
                'id': '123456789',
                'changes': [
                    {
                        'value': {
                            'messaging_product': 'whatsapp',
                            'metadata': {
                                'display_phone_number': '1234567890',
                                'phone_number_id': 'phone-id-1',
                            },
                            'contacts': [{'profile': {'name': 'Test User'}, 'wa_id': phone}],
                            'messages': [
                                {
                                    'from': phone,
                                    'id': f'wamid.test_{uuid.uuid4().hex}',
                                    'timestamp': '1700000000',
                                    'type': 'text',
                                    'text': {'body': text},
                                }
                            ],
                        },
                        'field': 'messages',
                    }
                ],
            }
        ],
    }


class TestConversationalSkuPriceLookup(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.phone = f'91987654{uuid.uuid4().hex[:4]}'
        conv = conversation_service.get_or_create_conversation(self.phone)
        conversation_service.clear_selection(conv.conversation_id)
        conversation_service.clear_pending_quote(conv.conversation_id)
        conversation_service.set_candidates(conv.conversation_id, [])
        order_service.clear_pending_quote(self.phone)

    def test_extract_sku_and_quantity_from_inquiry(self):
        cases = [
            ('what is the price for GS-135', 'GS-135', None),
            ('what is the price of GS-135', 'GS-135', None),
            ('price of GS-135', 'GS-135', None),
            ('how much is GS-135', 'GS-135', None),
            ('GS-135 price', 'GS-135', None),
            ('give me price for GS-135', 'GS-135', None),
            ('what about GS-135', 'GS-135', None),
            ('what is th e price for XG-GS-137', 'GS-137', None),
            ('what is the price for GS-135 for 100 units', 'GS-135', 100),
            ('how much for XG-501 500 pcs', 'XG-501', 500),
        ]
        for text, exp_sku, exp_qty in cases:
            with self.subTest(text=text):
                sku, qty = extract_sku_and_quantity_from_inquiry(text)
                self.assertEqual(sku, exp_sku, f'Failed SKU extraction for: {text}')
                self.assertEqual(qty, exp_qty, f'Failed quantity extraction for: {text}')

    def test_extract_sku_and_quantity_does_not_extract_from_plain_numbers_or_words(self):
        for s in ['200', 'what about 200', '50 units', 'hello', 'yes', 'okay', 'go ahead']:
            with self.subTest(text=s):
                sku, qty = extract_sku_and_quantity_from_inquiry(s)
                self.assertIsNone(sku)
                self.assertIsNone(qty)

    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_what_is_the_price_for_gs_135(self, mock_send):
        resp = self.client.post('/webhook', json=_make_webhook_payload('what is the price for GS-135', self.phone))
        self.assertEqual(resp.status_code, 200)
        args, kwargs = mock_send.call_args
        msg = kwargs.get('message') or args[1]
        self.assertNotIn('No products found', msg)
        self.assertIn('GS-135', msg)
        self.assertIn('quantity', msg.lower())
        conv = conversation_service.get_or_create_conversation(self.phone)
        self.assertEqual(conv.selected_sku, 'GS-135')

    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_price_of_gs_135(self, mock_send):
        resp = self.client.post('/webhook', json=_make_webhook_payload('price of GS-135', self.phone))
        self.assertEqual(resp.status_code, 200)
        args, kwargs = mock_send.call_args
        msg = kwargs.get('message') or args[1]
        self.assertNotIn('No products found', msg)
        self.assertIn('GS-135', msg)
        self.assertIn('quantity', msg.lower())

    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_how_much_is_gs_135(self, mock_send):
        resp = self.client.post('/webhook', json=_make_webhook_payload('how much is GS-135', self.phone))
        self.assertEqual(resp.status_code, 200)
        args, kwargs = mock_send.call_args
        msg = kwargs.get('message') or args[1]
        self.assertNotIn('No products found', msg)
        self.assertIn('GS-135', msg)
        self.assertIn('quantity', msg.lower())

    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_gs_135_price(self, mock_send):
        resp = self.client.post('/webhook', json=_make_webhook_payload('GS-135 price', self.phone))
        self.assertEqual(resp.status_code, 200)
        args, kwargs = mock_send.call_args
        msg = kwargs.get('message') or args[1]
        self.assertNotIn('No products found', msg)
        self.assertIn('GS-135', msg)
        self.assertIn('quantity', msg.lower())

    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_what_is_th_e_price_for_xg_gs_137(self, mock_send):
        resp = self.client.post('/webhook', json=_make_webhook_payload('what is th e price for XG-GS-137', self.phone))
        self.assertEqual(resp.status_code, 200)
        args, kwargs = mock_send.call_args
        msg = kwargs.get('message') or args[1]
        self.assertNotIn('No products found', msg)
        self.assertIn('GS-137', msg)
        self.assertIn('quantity', msg.lower())
        conv = conversation_service.get_or_create_conversation(self.phone)
        self.assertEqual(conv.selected_sku, 'GS-137')

    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_what_is_the_price_for_gs_135_for_100_units(self, mock_send):
        resp = self.client.post('/webhook', json=_make_webhook_payload('what is the price for GS-135 for 100 units', self.phone))
        self.assertEqual(resp.status_code, 200)
        args, kwargs = mock_send.call_args
        msg = kwargs.get('message') or args[1]
        self.assertNotIn('No products found', msg)
        self.assertIn('Pricing for GS-135 is not configured yet', msg)

    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_context_switch_from_gs_136_to_gs_135(self, mock_send):
        conv = conversation_service.get_or_create_conversation(self.phone)
        conversation_service.set_selected_product(conv.conversation_id, 'GS-136', None)

        mock_send.reset_mock()
        resp1 = self.client.post('/webhook', json=_make_webhook_payload('20', self.phone))
        self.assertEqual(resp1.status_code, 200)
        args1, kwargs1 = mock_send.call_args
        msg1 = kwargs1.get('message') or args1[1]
        self.assertIn('Pricing for GS-136 is not configured yet', msg1)

        mock_send.reset_mock()
        resp2 = self.client.post('/webhook', json=_make_webhook_payload('what is the price for GS-135', self.phone))
        self.assertEqual(resp2.status_code, 200)
        args2, kwargs2 = mock_send.call_args
        msg2 = kwargs2.get('message') or args2[1]
        self.assertNotIn('No products found', msg2)
        self.assertIn('GS-135', msg2)
        self.assertIn('quantity', msg2.lower())

        conv = conversation_service.get_or_create_conversation(self.phone)
        self.assertEqual(conv.selected_sku, 'GS-135', 'Context MUST switch to GS-135')

        mock_send.reset_mock()
        resp3 = self.client.post('/webhook', json=_make_webhook_payload('100', self.phone))
        self.assertEqual(resp3.status_code, 200)
        args3, kwargs3 = mock_send.call_args
        msg3 = kwargs3.get('message') or args3[1]
        self.assertIn('GS-135', msg3, 'Must quote GS-135')
        self.assertNotIn('GS-136', msg3, 'Must NOT remain locked to GS-136')

    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_context_switch_from_unpriced_to_priced_sku(self, mock_send):
        conv = conversation_service.get_or_create_conversation(self.phone)
        conversation_service.set_selected_product(conv.conversation_id, 'GS-136', None)

        resp1 = self.client.post('/webhook', json=_make_webhook_payload('how much is XG-501', self.phone))
        self.assertEqual(resp1.status_code, 200)

        mock_send.reset_mock()
        resp2 = self.client.post('/webhook', json=_make_webhook_payload('100', self.phone))
        self.assertEqual(resp2.status_code, 200)
        args, kwargs = mock_send.call_args
        msg = kwargs.get('message') or args[1]
        self.assertIn('Quotation for XG-501', msg)
        self.assertIn('410.00', msg)
        self.assertNotIn('GS-136', msg)

    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_what_about_200_follows_active_quote_quantity_change_path(self, mock_send):
        self.client.post('/webhook', json=_make_webhook_payload('XG-501 100', self.phone))
        mock_send.reset_mock()
        resp = self.client.post('/webhook', json=_make_webhook_payload('what about 200', self.phone))
        self.assertEqual(resp.status_code, 200)
        args, kwargs = mock_send.call_args
        msg = kwargs.get('message') or args[1]
        self.assertIn('Quotation for XG-501', msg)
        self.assertIn('200', msg)
        self.assertIn('410.00', msg)

    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_what_about_gs_135_switches_sku_context(self, mock_send):
        self.client.post('/webhook', json=_make_webhook_payload('XG-501 100', self.phone))
        mock_send.reset_mock()
        resp = self.client.post('/webhook', json=_make_webhook_payload('what about GS-135', self.phone))
        self.assertEqual(resp.status_code, 200)
        args, kwargs = mock_send.call_args
        msg = kwargs.get('message') or args[1]
        self.assertNotIn('No products found', msg)
        self.assertIn('GS-135', msg)
        self.assertIn('quantity', msg.lower())
        conv = conversation_service.get_or_create_conversation(self.phone)
        self.assertEqual(conv.selected_sku, 'GS-135')

    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_image_request_containing_sku_uses_image_path(self, mock_send):
        resp = self.client.post('/webhook', json=_make_webhook_payload('image XG-MP-01', self.phone))
        self.assertEqual(resp.status_code, 200)
        args, kwargs = mock_send.call_args
        msg = kwargs.get('message') or args[1]
        self.assertTrue('Photo:' in msg or 'Image' in msg or 'XG-MP-01' in msg)

    @patch('main.send_text_message', new_callable=AsyncMock)
    def test_exact_sku_retains_product_details_behavior(self, mock_send):
        resp = self.client.post('/webhook', json=_make_webhook_payload('XG-MP-01', self.phone))
        self.assertEqual(resp.status_code, 200)
        args, kwargs = mock_send.call_args
        msg = kwargs.get('message') or args[1]
        self.assertIn('XG-MP-01', msg)
        self.assertIn('Metal Pens', msg)
        self.assertIn('Based on quantity', msg)


if __name__ == '__main__':
    unittest.main(verbosity=2)
