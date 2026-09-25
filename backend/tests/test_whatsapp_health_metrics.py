import os
import unittest
import tempfile
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, AsyncMock

from services.audit_service import AuditService
from services.supabase_repository import SupabaseAuditRepository
from services.whatsapp_health_service import WhatsAppHealthService


class TestWhatsAppHealthMetrics(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.audit = AuditService(db_path=self.temp_db.name)

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except OSError:
                pass

    # 1. SQLite local audit
    def test_01_sqlite_local_audit(self):
        """Verifies local SQLite persistence: inserting and retrieving lifecycle audit events."""
        self.audit.record_event(
            correlation_id="local_corr_101",
            customer_phone="919840123456",
            direction="INBOUND",
            event_type="RECEIVED",
            status=None,
            wamid="wamid.prod_local_101",
            details={"text": "hello from sqlite"},
            is_test=False,
        )

        events = self.audit.get_recent_events(limit=5, production_only=False)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["correlation_id"], "local_corr_101")
        self.assertEqual(ev["customer_phone"], "919840123456")
        self.assertEqual(ev["direction"], "INBOUND")
        self.assertEqual(ev["event_type"], "RECEIVED")
        self.assertEqual(ev["wamid"], "wamid.prod_local_101")
        self.assertEqual(ev["details"], {"text": "hello from sqlite"})
        self.assertEqual(ev["is_test"], 0)
        self.assertTrue(ev["created_at"])

    # 2. Supabase production audit repository
    def test_02_supabase_production_audit_repository(self):
        """Verifies SupabaseAuditRepository records events and calculates metrics against Supabase."""
        mock_client = MagicMock()
        mock_client.is_configured = True
        mock_client.insert.return_value = [{"id": "uuid-1234"}]

        # Mock select return for get_metrics_summary: 1 received, 2 sent, 1 delivered, 1 read, 1 failed
        now_iso = datetime.now(timezone.utc).isoformat()
        mock_rows = [
            {"event_type": "RECEIVED", "direction": "INBOUND", "status": None, "customer_phone": "919840111111", "channel_message_id": "wamid.1", "created_at": now_iso, "details": {}},
            {"event_type": "META_ACCEPTED", "direction": "OUTBOUND", "status": "SENT", "customer_phone": "919840222222", "channel_message_id": "wamid.2", "created_at": now_iso, "details": {}},
            {"event_type": "SENT", "direction": "OUTBOUND", "status": "SENT", "customer_phone": "919840333333", "channel_message_id": "wamid.3", "created_at": now_iso, "details": {}},
            {"event_type": "DELIVERED", "direction": "OUTBOUND", "status": "DELIVERED", "customer_phone": "919840222222", "channel_message_id": "wamid.2", "created_at": now_iso, "details": {}},
            {"event_type": "READ", "direction": "OUTBOUND", "status": "READ", "customer_phone": "919840222222", "channel_message_id": "wamid.2", "created_at": now_iso, "details": {}},
            {"event_type": "FAILED", "direction": "OUTBOUND", "status": "FAILED", "customer_phone": "919840444444", "channel_message_id": "wamid.4", "created_at": now_iso, "details": {"error_code": 131026}},
        ]
        mock_client.select.return_value = mock_rows

        repo = SupabaseAuditRepository(client=mock_client, tenant_id="test_tenant")
        self.assertTrue(repo.is_configured)

        # 1. Test record_event
        record = repo.record_event(
            correlation_id="corr_sb_1",
            customer_phone="919840123456",
            direction="OUTBOUND",
            event_type="META_ACCEPTED",
            status="SENT",
            wamid="wamid.sb_outbound_1",
            details={"template": "order_confirmed"},
            is_test=False,
        )
        self.assertIsNotNone(record)
        mock_client.insert.assert_called_once()
        inserted_payload = mock_client.insert.call_args[0][1]
        self.assertEqual(inserted_payload["tenant_id"], "test_tenant")
        self.assertEqual(inserted_payload["correlation_id"], "corr_sb_1")
        self.assertEqual(inserted_payload["channel_message_id"], "wamid.sb_outbound_1")
        self.assertEqual(inserted_payload["is_test"], False)

        # 2. Test get_metrics_summary
        summary = repo.get_metrics_summary(hours=24, production_only=True)
        self.assertEqual(summary["total_received"], 1)
        self.assertEqual(summary["total_sent"], 2)  # META_ACCEPTED + SENT
        self.assertEqual(summary["total_delivered"], 1)
        self.assertEqual(summary["total_read"], 1)
        self.assertEqual(summary["total_failed"], 1)
        # Failure rate: 1 / (2 + 1) * 100 = 33.3%
        self.assertEqual(summary["failure_rate_percent"], 33.3)
        self.assertIsNotNone(summary["last_inbound"])
        self.assertIsNotNone(summary["last_outbound"])

    # 3. Production metric aggregation (rolling 24h window)
    def test_03_production_metric_aggregation(self):
        """Verifies rolling 24-hour window isolates events outside the window."""
        now = datetime.now(timezone.utc)
        recent_iso = (now - timedelta(hours=2)).isoformat()
        old_iso = (now - timedelta(hours=28)).isoformat()

        # Insert 1 event inside window and 1 event outside window directly
        with self.audit._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO message_audit_events (
                    correlation_id, customer_phone, direction, event_type, status, wamid, details, created_at, is_test
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("corr_recent", "919840111111", "INBOUND", "RECEIVED", None, "wamid.rec", "{}", recent_iso, 0),
            )
            cursor.execute(
                """
                INSERT INTO message_audit_events (
                    correlation_id, customer_phone, direction, event_type, status, wamid, details, created_at, is_test
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("corr_old", "919840222222", "INBOUND", "RECEIVED", None, "wamid.old", "{}", old_iso, 0),
            )
            conn.commit()

        # 24h summary should only include the recent one
        summary_24h = self.audit.get_metrics_summary(hours=24, production_only=True)
        self.assertEqual(summary_24h["total_received"], 1)

        # 48h summary should include both
        summary_48h = self.audit.get_metrics_summary(hours=48, production_only=True)
        self.assertEqual(summary_48h["total_received"], 2)

    # 4. Test-event exclusion
    def test_04_test_event_exclusion(self):
        """Verifies test events are completely excluded from production metrics."""
        # Record synthetic test events
        self.audit.record_event(
            correlation_id="corr_wamid.test_123",
            customer_phone="919876543210",
            direction="INBOUND",
            event_type="RECEIVED",
            is_test=True,
        )
        self.audit.record_status_event(
            wamid="wamid.status_test_fail",
            recipient="919876543210",
            status="failed",
            is_test=True,
        )

        # Production summary must be 0
        summary = self.audit.get_metrics_summary(hours=24, production_only=True)
        self.assertEqual(summary["total_received"], 0)
        self.assertEqual(summary["total_sent"], 0)
        self.assertEqual(summary["total_failed"], 0)
        self.assertIsNone(summary["failure_rate_percent"])

        # Non-production summary includes them
        all_summary = self.audit.get_metrics_summary(hours=24, production_only=False)
        self.assertEqual(all_summary["total_received"], 1)
        self.assertEqual(all_summary["total_failed"], 1)

    # 5. Failure rate
    def test_05_failure_rate(self):
        """Verifies calculation: failed / (sent + failed) * 100."""
        # 1 sent, 0 failed -> 0.0%
        self.audit.record_event(
            correlation_id="corr_succ_1",
            customer_phone="919840123456",
            direction="OUTBOUND",
            event_type="META_ACCEPTED",
            status="SENT",
            is_test=False,
        )
        s1 = self.audit.get_metrics_summary(hours=24, production_only=True)
        self.assertEqual(s1["failure_rate_percent"], 0.0)

        # 1 sent, 1 failed -> 1 / (1 + 1) = 50.0%
        self.audit.record_status_event(
            wamid="wamid.fail_1",
            recipient="919840123456",
            status="failed",
            is_test=False,
        )
        s2 = self.audit.get_metrics_summary(hours=24, production_only=True)
        self.assertEqual(s2["failure_rate_percent"], 50.0)

        # 2 sent, 1 failed -> 1 / (2 + 1) = 33.3%
        self.audit.record_event(
            correlation_id="corr_succ_2",
            customer_phone="919840123456",
            direction="OUTBOUND",
            event_type="META_ACCEPTED",
            status="SENT",
            is_test=False,
        )
        s3 = self.audit.get_metrics_summary(hours=24, production_only=True)
        self.assertEqual(s3["failure_rate_percent"], 33.3)

    # 6. Zero denominator
    def test_06_zero_denominator(self):
        """When total_sent + total_failed == 0, failure_rate_percent must be None."""
        summary = self.audit.get_metrics_summary(hours=24, production_only=True)
        self.assertEqual(summary["total_sent"], 0)
        self.assertEqual(summary["total_failed"], 0)
        self.assertIsNone(summary["failure_rate_percent"])

    # 7. Last inbound/outbound
    def test_07_last_inbound_outbound(self):
        """Verifies last_inbound and last_outbound retrieval and display fallbacks."""
        # Initially empty: returns None (UI displays 'No production messages yet')
        s0 = self.audit.get_metrics_summary(hours=24, production_only=True)
        self.assertIsNone(s0["last_inbound"])
        self.assertIsNone(s0["last_outbound"])

        # Record production inbound
        self.audit.record_event(
            correlation_id="in_1",
            customer_phone="919840111111",
            direction="INBOUND",
            event_type="RECEIVED",
            details={"message": "hi"},
            is_test=False,
        )
        s1 = self.audit.get_metrics_summary(hours=24, production_only=True)
        self.assertIsNotNone(s1["last_inbound"])
        self.assertEqual(s1["last_inbound"]["customer_phone"], "919840111111")
        self.assertIsNone(s1["last_outbound"])

        # Record production outbound
        self.audit.record_event(
            correlation_id="out_1",
            customer_phone="919840222222",
            direction="OUTBOUND",
            event_type="META_ACCEPTED",
            status="SENT",
            wamid="wamid.out_1",
            is_test=False,
        )
        s2 = self.audit.get_metrics_summary(hours=24, production_only=True)
        self.assertIsNotNone(s2["last_outbound"])
        self.assertEqual(s2["last_outbound"]["customer_phone"], "919840222222")

    # 8. Status event without phone
    def test_08_status_event_without_phone(self):
        """Status event without recipient phone resolves phone from prior outbound via WAMID."""
        # 1. Outbound recorded with phone
        self.audit.record_event(
            correlation_id="corr_out_target",
            customer_phone="919840999999",
            direction="OUTBOUND",
            event_type="META_ACCEPTED",
            status="SENT",
            wamid="wamid.resolve_test_phone_100",
            is_test=False,
        )

        # 2. Meta status arrives with recipient=None
        self.audit.record_status_event(
            wamid="wamid.resolve_test_phone_100",
            recipient=None,
            status="delivered",
            is_test=False,
        )

        # 3. Status record should have resolved customer_phone to '919840999999'
        events = self.audit.get_recent_events(limit=5, production_only=False)
        status_ev = next(e for e in events if e["event_type"] == "DELIVERED")
        self.assertEqual(status_ev["customer_phone"], "919840999999")
        self.assertEqual(status_ev["is_test"], 0)

    # 9. WAMID test-prefix detection
    def test_09_wamid_test_prefix_detection(self):
        """Known test prefixes on WAMID and correlation IDs are classified as tests."""
        test_wamids = [
            "wamid.test_12345",
            "wamid.status_test_6789",
            "wamid.crash_test_abc",
            "wamid.mock_event",
            "wamid.outbound_success_1",
            "test_wamid_123",
        ]
        for tw in test_wamids:
            self.assertEqual(
                self.audit._is_test_event(wamid=tw),
                1,
                f"Expected {tw} to be classified as test",
            )

        test_correlations = [
            "test_corr_1",
            "test_run_2",
            "corr_test_3",
            "corr_wamid.test_4",
            "corr_wamid.crash_test_5",
            "status_wamid.status_test_6",
        ]
        for tc in test_correlations:
            self.assertEqual(
                self.audit._is_test_event(correlation_id=tc),
                1,
                f"Expected correlation {tc} to be classified as test",
            )

    # 10. Legitimate-looking production WAMID not classified as test
    def test_10_legitimate_production_wamid_not_classified_as_test(self):
        """Real Meta production WAMIDs (even containing substring 'test') are NOT classified as tests."""
        # Meta base64 WAMID containing 'test' substring
        prod_wamid = "wamid.HBgMOTE5ODQwMTIzNDU2FQIAERgSMzUxQzM1OTFFRDRERTFCM0I1AA=="
        self.assertEqual(
            self.audit._is_test_event(wamid=prod_wamid, correlation_id="real_corr_888", phone="919840123456"),
            0,
        )

        # WAMID with substring 'test' in the middle of base64
        prod_wamid_with_substr = "wamid.HBgtestXYZ1234567890AA=="
        self.assertEqual(
            self.audit._is_test_event(wamid=prod_wamid_with_substr, correlation_id="real_corr_999", phone="919840123456"),
            0,
        )

        # Missing phone (None or UNKNOWN) does NOT mark an event as test
        self.assertEqual(
            self.audit._is_test_event(wamid=prod_wamid, correlation_id="real_corr_000", phone=None),
            0,
        )
        self.assertEqual(
            self.audit._is_test_event(wamid=prod_wamid, correlation_id="real_corr_000", phone="UNKNOWN"),
            0,
        )

    # Diagnostic Payload formatting
    def test_11_whatsapp_health_service_diagnostic_payload(self):
        """WhatsAppHealthService formats dashboard diagnostic safely with N/A on zero-denominator."""
        service = WhatsAppHealthService()
        with patch("services.whatsapp_health_service.audit_service", self.audit):
            with patch.object(service, "check_whatsapp_health", new_callable=AsyncMock) as mock_probe:
                mock_probe.return_value = {
                    "status": "healthy",
                    "webhook": {"configured": True},
                    "credentials": {"token_status": "valid", "token_type": "Bearer"},
                    "meta_api": {"reachable": True},
                }

                diag = asyncio.run(service.get_dashboard_diagnostic())
                self.assertEqual(diag["status"], "healthy")
                self.assertEqual(diag["metrics_24h"]["total_received"], 0)
                self.assertIsNone(diag["metrics_24h"]["failure_rate_percent"])
                self.assertIsNone(diag["last_incoming_message"])
                self.assertIsNone(diag["last_outbound_message"])


if __name__ == "__main__":
    unittest.main()
