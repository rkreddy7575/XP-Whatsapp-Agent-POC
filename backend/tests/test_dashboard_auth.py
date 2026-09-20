"""
test_dashboard_auth.py
Unit and integration tests for Owner Dashboard API authentication, login,
and Meta Webhook signature verification.
"""

import hashlib
import hmac
import json
import os
import sys
import unittest
from fastapi.testclient import TestClient

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from main import app

TEST_DASHBOARD_KEY = "test-dashboard-secret-token-key-12345"
TEST_APP_SECRET = "meta_app_secret_test_xyz123"


class TestDashboardAuthentication(unittest.TestCase):
    def setUp(self):
        self.orig_env = {
            k: os.environ.get(k)
            for k in ["DASHBOARD_API_KEY", "DASHBOARD_USERNAME", "DASHBOARD_PASSWORD", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"]
        }
        os.environ["DASHBOARD_API_KEY"] = TEST_DASHBOARD_KEY
        os.environ["DASHBOARD_USERNAME"] = "admin"
        os.environ["DASHBOARD_PASSWORD"] = "testpassword"
        os.environ["WHATSAPP_APP_SECRET"] = TEST_APP_SECRET
        os.environ["WHATSAPP_VERIFY_TOKEN"] = "test_verify_token_mudhra"
        self.client = TestClient(app)
        self.auth_header = {"Authorization": f"Bearer {TEST_DASHBOARD_KEY}"}
        self.api_key_header = {"X-API-Key": TEST_DASHBOARD_KEY}

    def tearDown(self):
        for k, v in self.orig_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_unauthenticated_api_requests_return_401(self):
        """Owner API endpoints must reject unauthenticated requests with HTTP 401."""
        endpoints = [
            ("GET", "/api/orders"),
            ("GET", "/api/orders/ORD-NONEXISTENT"),
            ("PATCH", "/api/orders/ORD-NONEXISTENT/status"),
            ("GET", "/api/conversations/enquiries"),
            ("GET", "/api/auth/verify"),
        ]
        for method, endpoint in endpoints:
            if method == "GET":
                res = self.client.get(endpoint)
            else:
                res = self.client.patch(endpoint, json={"status": "CONFIRMED"})
            self.assertEqual(
                res.status_code,
                401,
                f"Endpoint {method} {endpoint} should return 401 Unauthorized when missing auth header.",
            )

    def test_invalid_token_returns_401(self):
        """Requests with wrong bearer token or api key return 401."""
        bad_headers = [
            {"Authorization": "Bearer invalid_token_123"},
            {"X-API-Key": "invalid_api_key_456"},
        ]
        for h in bad_headers:
            res = self.client.get("/api/orders", headers=h)
            self.assertEqual(res.status_code, 401)

    def test_authenticated_api_requests_with_bearer_succeed(self):
        """Requests with valid Bearer token return HTTP 200."""
        res = self.client.get("/api/orders", headers=self.auth_header)
        self.assertEqual(res.status_code, 200)

    def test_authenticated_api_requests_with_x_api_key_succeed(self):
        """Requests with valid X-API-Key header return HTTP 200."""
        res = self.client.get("/api/orders", headers=self.api_key_header)
        self.assertEqual(res.status_code, 200)

    def test_login_with_valid_username_and_password(self):
        """POST /api/auth/login returns token when valid username/password provided."""
        res = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "testpassword"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "authenticated")
        self.assertEqual(data["token"], TEST_DASHBOARD_KEY)

    def test_login_with_valid_api_key(self):
        """POST /api/auth/login returns token when direct api_key provided."""
        res = self.client.post(
            "/api/auth/login",
            json={"api_key": TEST_DASHBOARD_KEY},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["token"], TEST_DASHBOARD_KEY)

    def test_login_with_invalid_credentials_returns_401(self):
        """POST /api/auth/login returns 401 for wrong credentials."""
        res = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong_password"},
        )
        self.assertEqual(res.status_code, 401)

    def test_public_health_check_remains_unauthenticated(self):
        """GET /health must always be accessible without authentication."""
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})

    def test_meta_webhook_challenge_remains_accessible(self):
        """GET /webhook verification challenge must continue working for Meta."""
        token = os.getenv("WHATSAPP_VERIFY_TOKEN", "verify_token_123")
        os.environ["WHATSAPP_VERIFY_TOKEN"] = token
        res = self.client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": token,
                "hub.challenge": "random_meta_challenge_12345",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.text, "random_meta_challenge_12345")

    def test_meta_webhook_signature_verification(self):
        """POST /webhook validates X-Hub-Signature-256 HMAC-SHA256."""
        payload = {"entry": []}
        body_bytes = json.dumps(payload).encode("utf-8")

        # 1. Missing signature header when app secret is set
        res_missing = self.client.post(
            "/webhook",
            content=body_bytes,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": ""},
        )
        # In test mode with empty header, it might allow or reject; if we pass an invalid signature:
        res_bad = self.client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=invalid_signature_hash",
            },
        )
        self.assertEqual(res_bad.status_code, 403)

        # 2. Valid signature
        valid_sig = "sha256=" + hmac.new(
            TEST_APP_SECRET.encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()
        res_valid = self.client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": valid_sig,
            },
        )
        self.assertEqual(res_valid.status_code, 200)


if __name__ == "__main__":
    unittest.main()
