import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure backend root is on sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi.testclient import TestClient
from main import app
from services.agent_router import agent_router
from services.catalogue_service import catalogue_service
from services.conversation_service import conversation_service
from services.gemini_models import IntentType, StructuredIntent
from services.order_models import OrderStatus
from services.order_service import order_service
from services.pricing_service import pricing_service
from services.whatsapp_service import (
    WhatsAppAPIError,
    WhatsAppConfigError,
    send_image_message,
)


class TestWhatsAppImageService(unittest.IsolatedAsyncioTestCase):
    """Unit tests for WhatsApp Cloud API image messaging capabilities."""

    @patch.dict(
        os.environ,
        {
            "WHATSAPP_ACCESS_TOKEN": "EAAG_TEST_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID": "1000999888",
            "META_GRAPH_API_VERSION": "v20.0",
        },
    )
    @patch("httpx.AsyncClient.post")
    async def test_send_image_message_success_with_caption(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "messaging_product": "whatsapp",
            "contacts": [{"wa_id": "919876543210"}],
            "messages": [{"id": "wamid.IMAGE_MSG_123"}],
        }
        mock_post.return_value = mock_resp

        result = await send_image_message(
            to="+919876543210",
            image_url="https://example.com/products/gs-001.jpg",
            caption="GS-001 Gift Set (2 in 1)",
        )

        self.assertEqual(result["messages"][0]["id"], "wamid.IMAGE_MSG_123")
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs["json"]
        self.assertEqual(payload["type"], "image")
        self.assertEqual(payload["to"], "919876543210")
        self.assertEqual(payload["image"]["link"], "https://example.com/products/gs-001.jpg")
        self.assertEqual(payload["image"]["caption"], "GS-001 Gift Set (2 in 1)")

    @patch.dict(
        os.environ,
        {
            "WHATSAPP_ACCESS_TOKEN": "EAAG_TEST_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID": "1000999888",
            "META_GRAPH_API_VERSION": "v20.0",
        },
    )
    @patch("httpx.AsyncClient.post")
    async def test_send_image_message_without_caption(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"messages": [{"id": "wamid.IMG_NO_CAP"}]}
        mock_post.return_value = mock_resp

        result = await send_image_message(
            to="919876543210",
            image_url="https://example.com/products/gs-002.jpg",
        )

        self.assertIn("messages", result)
        payload = mock_post.call_args[1]["json"]
        self.assertNotIn("caption", payload["image"])

    @patch.dict(
        os.environ,
        {
            "WHATSAPP_ACCESS_TOKEN": "EAAG_TEST_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID": "1000999888",
            "META_GRAPH_API_VERSION": "v20.0",
        },
    )
    @patch("httpx.AsyncClient.post")
    async def test_send_image_message_api_failure(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.is_success = False
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error": {
                "message": "Invalid Image URL provided",
                "type": "OAuthException",
                "code": 100,
            }
        }
        mock_post.return_value = mock_resp

        with self.assertRaises(WhatsAppAPIError) as ctx:
            await send_image_message(
                to="919876543210",
                image_url="https://example.com/invalid.jpg",
            )
        self.assertIn("Invalid Image URL provided", str(ctx.exception))

    @patch.dict(os.environ, {"WHATSAPP_ACCESS_TOKEN": ""}, clear=True)
    async def test_send_image_message_missing_config(self):
        with self.assertRaises(WhatsAppConfigError):
            await send_image_message(
                to="919876543210",
                image_url="https://example.com/image.jpg",
            )


class TestRichProductPresentation(unittest.TestCase):
    """Unit tests verifying rich visual WhatsApp product presentations."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        pricing_service.load_pricing_master()

    def _clean_test_state(self, phone: str):
        conv = conversation_service.get_or_create_conversation(phone)
        conversation_service.clear_selection(conv.conversation_id)
        conversation_service.clear_pending_quote(conv.conversation_id)
        order_service.clear_pending_quote(phone)
        with order_service._get_connection() as conn:
            conn.execute(
                "DELETE FROM order_items WHERE order_id IN (SELECT order_id FROM orders WHERE customer_phone = ?)",
                (phone,),
            )
            conn.execute("DELETE FROM orders WHERE customer_phone = ?", (phone,))
            conn.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = ?",
                (conv.conversation_id,),
            )
            conn.commit()

    def test_catalogue_format_product_presentation_with_images(self):
        """Verify presentation formatting when images are present."""
        sample_products = [
            {
                "sku": "GS-001",
                "name": "Luxury Executive Set",
                "category": "Gift Sets",
                "subcategory": "Gift Set (2 in 1)",
                "image_url": "https://example.com/images/gs-001.jpg",
                "colors": ["Black", "Brown"],
            },
            {
                "sku": "GS-002",
                "name": None,
                "category": "Gift Sets",
                "subcategory": "Gift Set (3 in 1)",
                "image_url": "https://example.com/images/gs-002.jpg",
                "colors": [],
            },
        ]

        text = catalogue_service.format_product_presentation(sample_products)

        self.assertIn("Found 2 matching products", text)
        self.assertIn("1️⃣ *Luxury Executive Set*", text)
        self.assertIn("SKU: `GS-001`", text)
        self.assertIn("Image: https://example.com/images/gs-001.jpg", text)
        self.assertIn("🎨 Options: Black, Brown", text)
        self.assertIn("2️⃣ *Gift Sets — Gift Set (3 in 1)*", text)
        self.assertIn("SKU: `GS-002`", text)
        self.assertIn("Image: https://example.com/images/gs-002.jpg", text)
        self.assertIn("Reply with the item number", text)

    def test_catalogue_format_product_presentation_fallback_without_images(self):
        """Verify fallback behavior: do not invent image URLs when none exist."""
        sample_products = [
            {
                "sku": "GS-001",
                "name": None,
                "category": "Gift Sets",
                "subcategory": "Gift Set (2 in 1)",
                "image_url": None,
                "colors": [],
            }
        ]

        text = catalogue_service.format_product_presentation(sample_products)

        self.assertIn("Found 1 matching products", text)
        self.assertIn("1️⃣ *Gift Sets — Gift Set (2 in 1)*", text)
        self.assertIn("SKU: `GS-001`", text)
        self.assertNotIn("Image:", text)
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)

    @patch("main.send_text_message", new_callable=AsyncMock)
    @patch("services.agent_router.gemini_service")
    def test_e2e_search_presentation_and_selection_flow(self, mock_gemini, mock_send_msg):
        """
        Verify end-to-end:
        1. Customer search presents 5 options in rich visual format.
        2. Pricing is NOT calculated or shown in presentation.
        3. Customer selects 'GS-002' or '2'.
        4. Authoritative quotation returned with PricingService calculation.
        5. Confirmation produces exact single order.
        """
        phone = "919811111199"
        self._clean_test_state(phone)
        mock_gemini.is_available.return_value = True

        # Turn 1: Customer asks for gift sets
        mock_gemini.parse_intent.return_value = StructuredIntent(
            intent=IntentType.PRODUCT_SEARCH,
            category="Gift Sets",
            quantity=100,
            budget_per_unit=500.0,
        )

        payload = {
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
                                "contacts": [{"profile": {"name": "Corporate Buyer"}, "wa_id": phone}],
                                "messages": [
                                    {
                                        "from": phone,
                                        "id": "wamid.SEARCH_001",
                                        "timestamp": "1726750000",
                                        "text": {"body": "I need 100 gift sets around ₹500"},
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

        resp = self.client.post("/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)

        sent_msg = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Found 5 matching products", sent_msg)
        self.assertIn("1️⃣", sent_msg)
        self.assertIn("2️⃣", sent_msg)
        self.assertIn("3️⃣", sent_msg)
        self.assertIn("4️⃣", sent_msg)
        self.assertIn("5️⃣", sent_msg)
        self.assertIn("SKU: `GS-001`", sent_msg)
        self.assertIn("SKU: `GS-002`", sent_msg)

        # Ensure pricing is NOT calculated or shown in the presentation
        self.assertNotIn("Grand Total", sent_msg)
        self.assertNotIn("Subtotal", sent_msg)

        # Turn 2: Customer selects option "2"
        select_payload = {
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
                                "contacts": [{"profile": {"name": "Corporate Buyer"}, "wa_id": phone}],
                                "messages": [
                                    {
                                        "from": phone,
                                        "id": "wamid.SELECT_002",
                                        "timestamp": "1726750010",
                                        "text": {"body": "2"},
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

        resp2 = self.client.post("/webhook", json=select_payload)
        self.assertEqual(resp2.status_code, 200)

        quote_msg = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Quotation for GS-002", quote_msg)
        self.assertIn("100 units", quote_msg)
        self.assertIn("Subtotal:", quote_msg)
        self.assertIn("Grand Total:", quote_msg)
        self.assertIn("Availability confirmation required", quote_msg)

        # Turn 3: Customer confirms order
        confirm_payload = {
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
                                "contacts": [{"profile": {"name": "Corporate Buyer"}, "wa_id": phone}],
                                "messages": [
                                    {
                                        "from": phone,
                                        "id": "wamid.CONFIRM_003",
                                        "timestamp": "1726750020",
                                        "text": {"body": "confirm"},
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

        resp3 = self.client.post("/webhook", json=confirm_payload)
        self.assertEqual(resp3.status_code, 200)
        confirm_msg = mock_send_msg.call_args[1].get("message", "")
        self.assertIn("Order Confirmed!", confirm_msg)

        orders = order_service.list_customer_orders(phone)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].items[0].sku, "GS-002")
        self.assertEqual(orders[0].items[0].quantity, 100)
        self.assertEqual(orders[0].status, OrderStatus.CONFIRMED)


if __name__ == "__main__":
    unittest.main()
