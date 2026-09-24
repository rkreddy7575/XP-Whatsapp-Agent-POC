import asyncio
import io
import json
import logging
import os
import sys
import unittest
import uuid
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from main import app
from services.audit_service import audit_service
from services.conversation_models import MessageDirection
from services.conversation_service import conversation_service
from services.order_service import order_service
from services.whatsapp_health_service import whatsapp_health_service
from services.whatsapp_service import (
    WhatsAppAPIError,
    WhatsAppConfigError,
    send_text_message,
)


def make_inbound_payload(body_text: str, message_id: str, sender: str = "919876543210") -> Dict[str, Any]:
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
                                    "id": message_id,
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


def make_status_payload(
    wamid: str,
    status_str: str,
    recipient_id: str = "919876543210",
    errors: Any = None,
) -> Dict[str, Any]:
    status_obj: Dict[str, Any] = {
        "id": wamid,
        "status": status_str,
        "timestamp": "1726750010",
        "recipient_id": recipient_id,
    }
    if errors:
        status_obj["errors"] = errors

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
                            "statuses": [status_obj],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


class TestWhatsAppProductionHardening(unittest.TestCase):
    """
    Comprehensive verification for Phase 9 requirements:
    1. Valid Meta token/API response
    2. Invalid/expired token detection
    3. Meta 400 permanent error (no retry)
    4. Meta 429 retry with backoff
    5. Meta 5xx retry with backoff
    6. Network timeout retry
    7. Successful outbound send
    8. Failed outbound send
    9. Duplicate inbound wamid
    10. Duplicate webhook event
    11. Duplicate order confirmation
    12. Status = sent
    13. Status = delivered
    14. Status = read
    15. Status = failed
    16. Incoming message persistence
    17. Processing failure after webhook receipt
    18. No secret/token in logs
    19. Safe health endpoints
    """

    def setUp(self):
        self.client = TestClient(app)
        self.mock_phone_id = "2096262090982740"

    # 1. Valid Meta token/API response
    @patch.dict(
        os.environ,
        {
            "WHATSAPP_ACCESS_TOKEN": "EAAG_TEST_TOKEN_SECRET_987654321",
            "WHATSAPP_PHONE_NUMBER_ID": "2096262090982740",
        },
    )
    def test_01_valid_meta_token_api_response(self):
        mock_resp = httpx.Response(
            status_code=200,
            json={
                "id": self.mock_phone_id,
                "verified_name": "Mudhra Gifting Solutions",
                "code_verification_status": "VERIFIED",
                "display_phone_number": "+1 555-138-7741",
                "quality_rating": "GREEN",
            },
            request=httpx.Request("GET", "https://graph.facebook.com"),
        )

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp
            health = asyncio.run(whatsapp_health_service.check_whatsapp_health(force_refresh=True))

            self.assertEqual(health["status"], "healthy")
            self.assertEqual(health["credentials"]["token_status"], "valid")
            self.assertEqual(health["meta_api"]["reachable"], True)
            self.assertIsNone(health["meta_api"]["error_code"])

    # 2. Invalid/expired token detection
    @patch.dict(
        os.environ,
        {
            "WHATSAPP_ACCESS_TOKEN": "EAAG_TEST_EXPIRED_TOKEN_ABC",
            "WHATSAPP_PHONE_NUMBER_ID": "2096262090982740",
        },
    )
    def test_02_invalid_expired_token_detection(self):
        mock_resp = httpx.Response(
            status_code=401,
            json={
                "error": {
                    "message": "Error validating access token: Session has expired on Thursday, 24-Sep-26 05:00:00 PDT.",
                    "type": "OAuthException",
                    "code": 190,
                    "error_subcode": 463,
                    "fbtrace_id": "ABC123Trace",
                }
            },
            request=httpx.Request("GET", "https://graph.facebook.com"),
        )

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp
            health = asyncio.run(whatsapp_health_service.check_whatsapp_health(force_refresh=True))

            self.assertEqual(health["status"], "unhealthy")
            self.assertEqual(health["credentials"]["token_status"], "expired")
            self.assertEqual(health["meta_api"]["error_code"], 190)
            self.assertEqual(health["meta_api"]["error_subcode"], 463)
            # Confirm token itself is NEVER present in health payload
            self.assertNotIn("EAAG_TEST_EXPIRED_TOKEN_ABC", json.dumps(health))

    # 3. Meta 400 permanent error (must not retry)
    @patch.dict(
        os.environ,
        {
            "WHATSAPP_ACCESS_TOKEN": "EAAG_TEST_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID": "2096262090982740",
        },
    )
    def test_03_meta_400_permanent_error_no_retry(self):
        mock_resp = httpx.Response(
            status_code=400,
            json={
                "error": {
                    "message": "(#100) Param text[body] must be a string",
                    "type": "OAuthException",
                    "code": 100,
                }
            },
            request=httpx.Request("POST", "https://graph.facebook.com"),
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp

            with self.assertRaises(WhatsAppAPIError):
                asyncio.run(send_text_message(to="919876543210", message="Hello"))

            # Must NOT retry 400 permanent error; called exactly once
            self.assertEqual(mock_post.call_count, 1)

    # 4. Meta 429 retry with backoff
    @patch.dict(
        os.environ,
        {
            "WHATSAPP_ACCESS_TOKEN": "EAAG_TEST_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID": "2096262090982740",
        },
    )
    def test_04_meta_429_retry_with_backoff(self):
        req = httpx.Request("POST", "https://graph.facebook.com")
        mock_resp_429 = httpx.Response(
            status_code=429,
            json={"error": {"message": "Rate limit exceeded", "type": "OAuthException", "code": 80007}},
            request=req,
        )
        mock_resp_200 = httpx.Response(
            status_code=200,
            json={"messaging_product": "whatsapp", "messages": [{"id": "wamid.OUTBOUND_RETRY_429"}]},
            request=req,
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = [mock_resp_429, mock_resp_200]
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = asyncio.run(send_text_message(to="919876543210", message="Rate test"))

                self.assertIn("messages", result)
                self.assertEqual(result["messages"][0]["id"], "wamid.OUTBOUND_RETRY_429")
                self.assertEqual(mock_post.call_count, 2)
                mock_sleep.assert_called_once()

    # 5. Meta 5xx retry with backoff
    @patch.dict(
        os.environ,
        {
            "WHATSAPP_ACCESS_TOKEN": "EAAG_TEST_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID": "2096262090982740",
        },
    )
    def test_05_meta_5xx_retry_with_backoff(self):
        req = httpx.Request("POST", "https://graph.facebook.com")
        mock_resp_503 = httpx.Response(status_code=503, text="Service Temporarily Unavailable", request=req)
        mock_resp_200 = httpx.Response(
            status_code=200,
            json={"messaging_product": "whatsapp", "messages": [{"id": "wamid.OUTBOUND_RETRY_503"}]},
            request=req,
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = [mock_resp_503, mock_resp_200]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = asyncio.run(send_text_message(to="919876543210", message="503 test"))

                self.assertIn("messages", result)
                self.assertEqual(result["messages"][0]["id"], "wamid.OUTBOUND_RETRY_503")
                self.assertEqual(mock_post.call_count, 2)

    # 6. Network timeout retry
    @patch.dict(
        os.environ,
        {
            "WHATSAPP_ACCESS_TOKEN": "EAAG_TEST_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID": "2096262090982740",
        },
    )
    def test_06_network_timeout_retry(self):
        req = httpx.Request("POST", "https://graph.facebook.com")
        mock_resp_200 = httpx.Response(
            status_code=200,
            json={"messaging_product": "whatsapp", "messages": [{"id": "wamid.OUTBOUND_TIMEOUT_RECOVERED"}]},
            request=req,
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = [
                httpx.TimeoutException("Read timed out"),
                mock_resp_200,
            ]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = asyncio.run(send_text_message(to="919876543210", message="Timeout test"))

                self.assertIn("messages", result)
                self.assertEqual(result["messages"][0]["id"], "wamid.OUTBOUND_TIMEOUT_RECOVERED")
                self.assertEqual(mock_post.call_count, 2)

    # 7. Successful outbound send
    @patch.dict(
        os.environ,
        {
            "WHATSAPP_ACCESS_TOKEN": "EAAG_TEST_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID": "2096262090982740",
        },
    )
    def test_07_successful_outbound_send(self):
        req = httpx.Request("POST", "https://graph.facebook.com")
        mock_resp_200 = httpx.Response(
            status_code=200,
            json={
                "messaging_product": "whatsapp",
                "contacts": [{"input": "919876543210", "wa_id": "919876543210"}],
                "messages": [{"id": "wamid.OUTBOUND_SUCCESS_777"}],
            },
            request=req,
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp_200
            corr_id = f"test_corr_{uuid.uuid4().hex[:8]}"

            result = asyncio.run(
                send_text_message(
                    to="919876543210",
                    message="Order ready",
                    correlation_id=corr_id,
                )
            )

            self.assertIn("messages", result)
            self.assertEqual(result["messages"][0]["id"], "wamid.OUTBOUND_SUCCESS_777")

            # Check audit event was recorded
            events = audit_service.get_recent_events(correlation_id=corr_id)
            event_types = [e["event_type"] for e in events]
            self.assertIn("SEND_ATTEMPT", event_types)
            self.assertIn("META_ACCEPTED", event_types)

    # 8. Failed outbound send (after bounded retries)
    @patch.dict(
        os.environ,
        {
            "WHATSAPP_ACCESS_TOKEN": "EAAG_TEST_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID": "2096262090982740",
        },
    )
    def test_08_failed_outbound_send_bounded(self):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.ConnectError("Network unreachable")
            with patch("asyncio.sleep", new_callable=AsyncMock):
                corr_id = f"test_corr_{uuid.uuid4().hex[:8]}"

                with self.assertRaises(WhatsAppAPIError):
                    asyncio.run(
                        send_text_message(
                            to="919876543210",
                            message="Will fail",
                            correlation_id=corr_id,
                        )
                    )

                # Total 3 attempts (1 initial + 2 retries)
                self.assertEqual(mock_post.call_count, 3)

                # Check FAILED audit event was recorded
                events = audit_service.get_recent_events(correlation_id=corr_id)
                event_types = [e["event_type"] for e in events]
                self.assertIn("FAILED", event_types)

    # 9. Duplicate inbound wamid (webhook idempotency)
    def test_09_duplicate_inbound_wamid(self):
        test_wamid = f"wamid.TEST_DUP_INBOUND_{uuid.uuid4().hex[:8]}"
        payload = make_inbound_payload("Hello", test_wamid)

        with patch("main.send_text_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"messages": [{"id": "wamid.OUT_1"}]}

            # First delivery
            resp1 = self.client.post("/webhook", json=payload)
            self.assertEqual(resp1.status_code, 200)
            self.assertEqual(mock_send.call_count, 1)

            # Second duplicate delivery
            resp2 = self.client.post("/webhook", json=payload)
            self.assertEqual(resp2.status_code, 200)
            # Must NOT call send_text_message a second time
            self.assertEqual(mock_send.call_count, 1)

    # 10. Duplicate webhook event in cache & store
    def test_10_duplicate_webhook_event_cache(self):
        test_wamid = f"wamid.TEST_DEDUP_CACHE_{uuid.uuid4().hex[:8]}"
        self.assertFalse(conversation_service.is_message_processed(test_wamid))

        conversation_service.mark_message_processing(test_wamid)
        self.assertTrue(conversation_service.is_message_processed(test_wamid))

    # 11. Duplicate order confirmation
    def test_11_duplicate_order_confirmation(self):
        sender = f"9199{uuid.uuid4().int % 100000000:08d}"
        wamid_quote = f"wamid.ORDER_SETUP_{uuid.uuid4().hex[:8]}"
        wamid_confirm1 = f"wamid.ORDER_CONFIRM1_{uuid.uuid4().hex[:8]}"
        wamid_confirm2 = f"wamid.ORDER_CONFIRM2_{uuid.uuid4().hex[:8]}"

        with patch("main.send_text_message", new_callable=AsyncMock) as mock_send_txt:
            mock_send_txt.return_value = {"messages": [{"id": "wamid.CONFIRM_OUT"}]}

            # 1. Ask for quote
            res_quote = self.client.post(
                "/webhook",
                json=make_inbound_payload("XG-501 10", message_id=wamid_quote, sender=sender),
            )
            self.assertEqual(res_quote.status_code, 200)

            # 2. Confirm order first time
            res_confirm1 = self.client.post(
                "/webhook",
                json=make_inbound_payload("CONFIRM", message_id=wamid_confirm1, sender=sender),
            )
            self.assertEqual(res_confirm1.status_code, 200)

            # Count orders
            orders_after_first = [o for o in order_service.list_all_orders() if o.customer_phone == sender]
            self.assertEqual(len(orders_after_first), 1)

            # 3. Try confirming again
            res_confirm2 = self.client.post(
                "/webhook",
                json=make_inbound_payload("CONFIRM", message_id=wamid_confirm2, sender=sender),
            )
            self.assertEqual(res_confirm2.status_code, 200)

            # Order count must still be exactly 1
            orders_after_second = [o for o in order_service.list_all_orders() if o.customer_phone == sender]
            self.assertEqual(len(orders_after_second), 1)

    # 12. Status = sent
    def test_12_status_sent(self):
        test_wamid = f"wamid.STATUS_TEST_SENT_{uuid.uuid4().hex[:8]}"
        sender = f"9198{uuid.uuid4().int % 100000000:08d}"
        conv = conversation_service.get_or_create_conversation(sender)
        conversation_service.add_message(
            conv.conversation_id,
            MessageDirection.OUTBOUND,
            "Quote details",
            channel_message_id=test_wamid,
        )

        payload = make_status_payload(test_wamid, "sent", recipient_id=sender)
        resp = self.client.post("/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)

        # Check status was updated
        messages = conversation_service.get_recent_messages(conv.conversation_id, limit=5)
        target_msg = next((m for m in messages if m.channel_message_id == test_wamid), None)
        self.assertIsNotNone(target_msg)
        self.assertEqual(target_msg.status, "SENT")

        # Check audit event
        events = audit_service.get_recent_events(wamid=test_wamid)
        self.assertTrue(any(e["event_type"] == "SENT" or e.get("status") == "SENT" for e in events))

    # 13. Status = delivered
    def test_13_status_delivered(self):
        test_wamid = f"wamid.STATUS_TEST_DELIVERED_{uuid.uuid4().hex[:8]}"
        sender = f"9198{uuid.uuid4().int % 100000000:08d}"
        conv = conversation_service.get_or_create_conversation(sender)
        conversation_service.add_message(
            conv.conversation_id,
            MessageDirection.OUTBOUND,
            "Order dispatch update",
            channel_message_id=test_wamid,
        )

        payload = make_status_payload(test_wamid, "delivered", recipient_id=sender)
        resp = self.client.post("/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)

        messages = conversation_service.get_recent_messages(conv.conversation_id, limit=5)
        target_msg = next((m for m in messages if m.channel_message_id == test_wamid), None)
        self.assertIsNotNone(target_msg)
        self.assertEqual(target_msg.status, "DELIVERED")

        events = audit_service.get_recent_events(wamid=test_wamid)
        self.assertTrue(any(e["event_type"] == "DELIVERED" or e.get("status") == "DELIVERED" for e in events))

    # 14. Status = read
    def test_14_status_read(self):
        test_wamid = f"wamid.STATUS_TEST_READ_{uuid.uuid4().hex[:8]}"
        sender = f"9198{uuid.uuid4().int % 100000000:08d}"
        conv = conversation_service.get_or_create_conversation(sender)
        conversation_service.add_message(
            conv.conversation_id,
            MessageDirection.OUTBOUND,
            "Catalog card",
            channel_message_id=test_wamid,
        )

        payload = make_status_payload(test_wamid, "read", recipient_id=sender)
        resp = self.client.post("/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)

        messages = conversation_service.get_recent_messages(conv.conversation_id, limit=5)
        target_msg = next((m for m in messages if m.channel_message_id == test_wamid), None)
        self.assertIsNotNone(target_msg)
        self.assertEqual(target_msg.status, "READ")

        events = audit_service.get_recent_events(wamid=test_wamid)
        self.assertTrue(any(e["event_type"] == "READ" or e.get("status") == "READ" for e in events))

    # 15. Status = failed
    def test_15_status_failed(self):
        test_wamid = f"wamid.STATUS_TEST_FAILED_{uuid.uuid4().hex[:8]}"
        sender = f"9198{uuid.uuid4().int % 100000000:08d}"
        conv = conversation_service.get_or_create_conversation(sender)
        conversation_service.add_message(
            conv.conversation_id,
            MessageDirection.OUTBOUND,
            "Message to unreachable recipient",
            channel_message_id=test_wamid,
        )

        error_details = [
            {
                "code": 131056,
                "title": "Recipient phone number not in allowed list",
                "message": "Message Undeliverable",
            }
        ]
        payload = make_status_payload(test_wamid, "failed", recipient_id=sender, errors=error_details)
        resp = self.client.post("/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)

        messages = conversation_service.get_recent_messages(conv.conversation_id, limit=5)
        target_msg = next((m for m in messages if m.channel_message_id == test_wamid), None)
        self.assertIsNotNone(target_msg)
        self.assertEqual(target_msg.status, "FAILED")

        events = audit_service.get_recent_events(wamid=test_wamid)
        self.assertTrue(any(e["event_type"] == "FAILED" or e.get("status") == "FAILED" for e in events))

    # 16. Incoming message persistence
    def test_16_incoming_message_persistence(self):
        test_wamid = f"wamid.INCOMING_PERSIST_{uuid.uuid4().hex[:8]}"
        phone = f"91988{uuid.uuid4().int % 10000000:07d}"
        payload = make_inbound_payload("Need 50 gift sets", test_wamid, sender=phone)

        with patch("main.send_text_message", new_callable=AsyncMock):
            resp = self.client.post("/webhook", json=payload)
            self.assertEqual(resp.status_code, 200)

            conv = conversation_service.get_conversation_by_phone(phone)
            self.assertIsNotNone(conv)
            messages = conversation_service.get_recent_messages(conv.conversation_id, limit=5)
            inbound_msg = next((m for m in messages if m.channel_message_id == test_wamid), None)
            self.assertIsNotNone(inbound_msg)
            self.assertEqual(inbound_msg.direction, MessageDirection.INBOUND)
            self.assertEqual(inbound_msg.message_text, "Need 50 gift sets")

    # 17. Processing failure after webhook receipt (graceful return 200 to Meta)
    def test_17_processing_failure_after_webhook_receipt(self):
        test_wamid = f"wamid.CRASH_TEST_{uuid.uuid4().hex[:8]}"
        payload = make_inbound_payload("Trigger Error", test_wamid)

        with patch(
            "services.agent_router.agent_router.handle_incoming_message",
            side_effect=RuntimeError("Simulated LLM pipeline crash"),
        ):
            resp = self.client.post("/webhook", json=payload)
            # Meta MUST always receive HTTP 200
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"status": "ok"})

    # 18. No secret/token appears in logs
    def test_18_no_secret_token_in_logs(self):
        secret_token = "SECRET_TOKEN_DO_NOT_PRINT_12345ABC"

        log_capture = io.StringIO()
        handler = logging.StreamHandler(log_capture)
        logging.getLogger().addHandler(handler)

        try:
            with patch.dict(os.environ, {"WHATSAPP_ACCESS_TOKEN": secret_token}):
                req = httpx.Request("GET", "https://graph.facebook.com")
                mock_resp = httpx.Response(
                    status_code=401,
                    json={
                        "error": {
                            "message": "The access token could not be decrypted",
                            "type": "OAuthException",
                            "code": 190,
                        }
                    },
                    request=req,
                )
                with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
                    mock_get.return_value = mock_resp
                    asyncio.run(whatsapp_health_service.check_whatsapp_health(force_refresh=True))

            captured_output = log_capture.getvalue()
            # Assert secret token value NEVER leaked into logs
            self.assertNotIn(secret_token, captured_output)
            self.assertNotIn("Bearer " + secret_token, captured_output)
        finally:
            logging.getLogger().removeHandler(handler)

    # 19. WhatsApp Health endpoints
    def test_19_whatsapp_health_endpoints(self):
        # 1. Public GET /health/whatsapp (safe summary)
        resp_public = self.client.get("/health/whatsapp")
        self.assertEqual(resp_public.status_code, 200)
        data = resp_public.json()
        self.assertIn("status", data)
        self.assertIn("webhook", data)
        self.assertIn("credentials", data)
        self.assertIn("meta_api", data)
        # Token must never be returned
        self.assertNotIn("token", data)
        self.assertNotIn("access_token", data)

        # 2. Authenticated GET /api/dashboard/whatsapp-health
        resp_auth = self.client.get(
            "/api/dashboard/whatsapp-health",
            headers={"Authorization": f"Bearer {os.getenv('DASHBOARD_API_KEY', 'test-dashboard-secret-token-key-12345')}"},
        )
        self.assertEqual(resp_auth.status_code, 200)
        auth_data = resp_auth.json()
        self.assertIn("metrics_24h", auth_data)
        self.assertIn("recent_failures", auth_data)


if __name__ == "__main__":
    unittest.main()
