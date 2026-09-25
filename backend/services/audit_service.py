"""
Audit Service for WhatsApp Message Observability and Lifecycle Tracking.

Tracks the complete lifecycle of customer conversations:
  RECEIVED -> PERSISTED -> ROUTING_STARTED -> ROUTING_COMPLETED ->
  SEND_ATTEMPT -> META_ACCEPTED -> SENT -> DELIVERED -> READ (or FAILED)

Ensures zero secret leakage: token values and authorization headers are never recorded.
Distinguishes real production customer traffic from automated test/synthetic runs.

Persistence Architecture:
- LOCAL: SQLite for development and automated test suites.
- PRODUCTION: Supabase PostgreSQL for durable message_audit_events and 24h metrics.
"""

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default SQLite paths with automatic test environment isolation
DEFAULT_PROD_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "app.db")
)
DEFAULT_TEST_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "test_app.db")
)


def get_default_db_path() -> str:
    """
    Returns the appropriate SQLite path based on configuration and test environment.
    Respects Vercel's DATABASE_PATH=/tmp/app.db and isolates automated tests from app.db.
    """
    db_env = os.getenv("DATABASE_PATH") or os.getenv("SQLITE_DB_PATH")
    if db_env:
        if db_env == ":memory:":
            return ":memory:"
        return os.path.abspath(db_env)
    if os.getenv("TESTING") in ("1", "true", "True") or any(
        "unittest" in str(arg).lower() or "pytest" in str(arg).lower()
        for arg in sys.argv
    ):
        return DEFAULT_TEST_DB_PATH
    return DEFAULT_PROD_DB_PATH


# Preserved for backward compatibility
DEFAULT_DB_PATH = DEFAULT_PROD_DB_PATH

KNOWN_TEST_WAMID_PREFIXES = (
    "wamid.test_",
    "wamid.test",
    "wamid.status_test_",
    "wamid.crash_test_",
    "wamid.mock_",
    "wamid.outbound_success_",
    "wamid.duplicate_",
    "wamid.incoming_persist_",
    "wamid.order_confirm",
    "wamid.order_setup",
    "test_wamid",
)

KNOWN_TEST_CORRELATION_PREFIXES = (
    "test_corr_",
    "test_",
    "corr_test_",
    "corr_wamid.test_",
    "corr_wamid.crash_test_",
    "status_wamid.status_test_",
    "corr_mock_",
    "corr_wamid.order_confirm",
    "corr_wamid.order_setup",
    "corr_wamid.incoming_persist_",
)

KNOWN_TEST_PHONES = {
    "912379898745",
    "919876543210",
    "1234567890",
    "9876543210",
}


class AuditService:
    def __init__(
        self,
        db_path: Optional[str] = None,
        supabase_repo: Optional[Any] = None,
        tenant_id: str = "default",
    ):
        self._explicit_db_path = db_path
        self.tenant_id = tenant_id
        self._supabase_repo = supabase_repo
        # Initialize SQLite tables if SQLite is used
        if not self.is_supabase_primary and self.db_path != ":memory:":
            self._ensure_tables()

    @property
    def db_path(self) -> str:
        if self._explicit_db_path is not None:
            return self._explicit_db_path
        return get_default_db_path()

    @property
    def is_supabase_primary(self) -> bool:
        """
        Returns True if Supabase is configured and serves as primary persistence.
        Automated test runs default to isolated SQLite unless USE_SUPABASE_IN_TESTS=1.
        """
        if self._explicit_db_path is not None:
            return False
        if any("unittest" in str(arg).lower() or "pytest" in str(arg).lower() for arg in sys.argv):
            if os.getenv("USE_SUPABASE_IN_TESTS") not in ("1", "true", "True"):
                return False
        try:
            from services.supabase_repository import SupabaseClient
            return SupabaseClient().is_configured
        except Exception:
            return False

    @property
    def supabase_repo(self) -> Any:
        if self._supabase_repo is None:
            from services.supabase_repository import SupabaseAuditRepository
            self._supabase_repo = SupabaseAuditRepository(tenant_id=self.tenant_id)
        return self._supabase_repo

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        """Initializes SQLite schema for message audit events with test isolation."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS message_audit_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        correlation_id TEXT NOT NULL,
                        customer_phone TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        status TEXT,
                        wamid TEXT,
                        details TEXT DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        is_test INTEGER DEFAULT 0
                    );
                """)

                # Check if is_test column exists (for backward compatibility migration)
                cursor.execute("PRAGMA table_info(message_audit_events);")
                columns = [col["name"] for col in cursor.fetchall()]
                if "is_test" not in columns:
                    cursor.execute("ALTER TABLE message_audit_events ADD COLUMN is_test INTEGER DEFAULT 0;")

                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_audit_correlation
                    ON message_audit_events (correlation_id);
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_audit_wamid
                    ON message_audit_events (wamid);
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_audit_phone
                    ON message_audit_events (customer_phone);
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_audit_created_at
                    ON message_audit_events (created_at);
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_audit_is_test
                    ON message_audit_events (is_test);
                """)

                # Backfill all legacy test clutter generated prior to test-isolation tracking
                cursor.execute("UPDATE message_audit_events SET is_test = 1 WHERE is_test = 0;")
                conn.commit()
        except Exception as exc:
            logger.error("Failed to initialize message_audit_events table: %s", exc)

    def _is_test_event(
        self,
        correlation_id: str = "",
        wamid: Optional[str] = None,
        phone: Optional[str] = None,
        is_test: Optional[bool] = None,
    ) -> int:
        """
        Determines whether an audit event is from automated tests or synthetic runs.
        Uses explicit known test prefixes/patterns. Never uses broad substring matching
        that could classify legitimate Meta WAMIDs containing 'test' as tests.
        """
        if is_test is not None:
            return 1 if is_test else 0

        cid = str(correlation_id or "").lower()
        w_id = str(wamid or "").lower()
        clean_p = str(phone or "").lstrip("+").strip()

        # Explicit known test prefixes on WAMID (never broad substring)
        if w_id and any(w_id.startswith(prefix) for prefix in KNOWN_TEST_WAMID_PREFIXES):
            return 1

        # Explicit known test prefixes on correlation ID
        if cid and any(cid.startswith(prefix) for prefix in KNOWN_TEST_CORRELATION_PREFIXES):
            return 1

        # Known synthetic phone numbers (empty phone or UNKNOWN is NOT a test!)
        if clean_p in KNOWN_TEST_PHONES:
            return 1

        return 0

    def record_event(
        self,
        correlation_id: str,
        customer_phone: Optional[str] = None,
        direction: str = "INBOUND",
        event_type: str = "UNKNOWN",
        status: Optional[str] = None,
        wamid: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        phone: Optional[str] = None,
        is_test: Optional[bool] = None,
    ) -> None:
        """
        Records a granular lifecycle event in the audit store.
        Never stores secret credentials.
        Routes to Supabase in production and SQLite in local/test environments.
        """
        raw_phone = phone if phone is not None else customer_phone
        clean_phone = raw_phone.lstrip("+").strip() if raw_phone else "UNKNOWN"
        safe_details = details or {}
        sanitized_details = self._sanitize_dict(safe_details)

        test_flag = self._is_test_event(correlation_id, wamid, clean_phone, is_test)

        if self.is_supabase_primary:
            try:
                self.supabase_repo.record_event(
                    correlation_id=correlation_id,
                    customer_phone=clean_phone,
                    direction=direction,
                    event_type=event_type,
                    status=status,
                    wamid=wamid,
                    details=sanitized_details,
                    is_test=bool(test_flag),
                )
            except Exception as exc:
                logger.error("Supabase audit persistence failed for %s (%s): %s", correlation_id, event_type, exc)
            return

        # Local SQLite persistence
        now_iso = datetime.now(timezone.utc).isoformat()
        details_json = json.dumps(sanitized_details)

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO message_audit_events (
                        correlation_id, customer_phone, direction, event_type,
                        status, wamid, details, created_at, is_test
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        correlation_id,
                        clean_phone,
                        direction,
                        event_type,
                        status,
                        wamid,
                        details_json,
                        now_iso,
                        test_flag,
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.error("Failed to persist audit event (%s, %s): %s", correlation_id, event_type, exc)

    def record_status_event(
        self,
        wamid: str,
        recipient: Optional[str] = None,
        status: str = "",
        errors: Optional[List[Dict[str, Any]]] = None,
        raw_timestamp: Optional[str] = None,
        recipient_id: Optional[str] = None,
        is_test: Optional[bool] = None,
    ) -> None:
        """
        Records a Meta status update webhook (sent, delivered, read, failed).
        Updates conversation message status if corresponding record exists.
        Resolves customer phone from previous outbound record if missing.
        """
        if not wamid:
            return

        target_phone = recipient_id or recipient
        clean_phone = target_phone.lstrip("+").strip() if target_phone else ""

        # Lookup recipient phone from previous outbound event if not in status payload
        if not clean_phone or clean_phone == "UNKNOWN":
            if self.is_supabase_primary:
                found_phone = self.supabase_repo.lookup_phone_by_wamid(wamid)
                if found_phone:
                    clean_phone = found_phone
            else:
                found_phone = self._lookup_phone_by_wamid_sqlite(wamid)
                if found_phone:
                    clean_phone = found_phone

        if not clean_phone:
            clean_phone = "UNKNOWN"

        canonical_status = status.upper()
        details: Dict[str, Any] = {
            "meta_status": status,
            "raw_timestamp": raw_timestamp,
        }
        if errors:
            details["errors"] = [
                {
                    "code": err.get("code"),
                    "title": err.get("title"),
                    "message": err.get("message"),
                }
                for err in errors
            ]

        correlation_id = f"status_{wamid}"
        test_flag = self._is_test_event(correlation_id, wamid, clean_phone, is_test)

        if self.is_supabase_primary:
            try:
                self.supabase_repo.record_event(
                    correlation_id=correlation_id,
                    customer_phone=clean_phone,
                    direction="OUTBOUND",
                    event_type=canonical_status,
                    status=canonical_status,
                    wamid=wamid,
                    details=details,
                    is_test=bool(test_flag),
                )
            except Exception as exc:
                logger.error("Supabase status audit persistence failed for %s (%s): %s", wamid, canonical_status, exc)
            return

        # Local SQLite persistence
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO message_audit_events (
                        correlation_id, customer_phone, direction, event_type,
                        status, wamid, details, created_at, is_test
                    ) VALUES (?, ?, 'OUTBOUND', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        correlation_id,
                        clean_phone,
                        canonical_status,
                        canonical_status,
                        wamid,
                        json.dumps(details),
                        now_iso,
                        test_flag,
                    ),
                )

                # Update status in conversation_messages if column exists
                try:
                    cursor.execute(
                        "UPDATE conversation_messages SET status = ? WHERE channel_message_id = ?",
                        (canonical_status, wamid),
                    )
                except sqlite3.OperationalError:
                    pass

                conn.commit()
        except Exception as exc:
            logger.error("Failed to record status event for wamid %s: %s", wamid, exc)

    def _lookup_phone_by_wamid_sqlite(self, wamid: str) -> Optional[str]:
        """Looks up the customer phone associated with an outbound wamid in SQLite."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT customer_phone FROM message_audit_events WHERE wamid = ? AND customer_phone != 'UNKNOWN' LIMIT 1",
                    (wamid,),
                )
                row = cursor.fetchone()
                if row and row["customer_phone"]:
                    return row["customer_phone"]
        except Exception:
            pass
        return None

    def get_events_for_correlation(self, correlation_id: str) -> List[Dict[str, Any]]:
        """Returns chronological list of events for a specific message/turn correlation."""
        if self.is_supabase_primary:
            return self.supabase_repo.get_recent_events(correlation_id=correlation_id, limit=50)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM message_audit_events
                WHERE correlation_id = ?
                ORDER BY id ASC
                """,
                (correlation_id,),
            )
            rows = cursor.fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_events_for_phone(self, phone: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns chronological list of recent events for a customer phone number."""
        clean_phone = phone.lstrip("+").strip()
        if self.is_supabase_primary:
            return self.supabase_repo.get_recent_events(phone=clean_phone, limit=limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM message_audit_events
                WHERE customer_phone = ?
                ORDER BY id DESC LIMIT ?
                """,
                (clean_phone, limit),
            )
            rows = cursor.fetchall()
            return [self._row_to_dict(r) for r in reversed(rows)]

    def get_recent_events(
        self,
        limit: int = 50,
        correlation_id: Optional[str] = None,
        wamid: Optional[str] = None,
        phone: Optional[str] = None,
        event_type: Optional[str] = None,
        production_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Returns recent audit events across the platform, optionally filtered."""
        if self.is_supabase_primary:
            return self.supabase_repo.get_recent_events(
                limit=limit,
                correlation_id=correlation_id,
                wamid=wamid,
                phone=phone,
                event_type=event_type,
                production_only=production_only,
            )

        clauses = []
        params: List[Any] = []
        if production_only and not correlation_id and not wamid:
            clauses.append("is_test = 0")
        if correlation_id:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)
        if wamid:
            clauses.append("wamid = ?")
            params.append(wamid)
        if phone:
            clauses.append("customer_phone = ?")
            params.append(phone.lstrip("+").strip())
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)

        where_str = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM message_audit_events {where_str} ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_last_event_by_direction(self, direction: str, production_only: bool = True) -> Optional[Dict[str, Any]]:
        """Returns the single most recent audit event for a given direction (INBOUND/OUTBOUND)."""
        if self.is_supabase_primary:
            return self.supabase_repo.get_last_event_by_direction(direction, production_only=production_only)

        where_clauses = ["direction = ?"]
        params: List[Any] = [direction.upper()]
        if production_only:
            where_clauses.append("is_test = 0")
            where_clauses.append("customer_phone != 'UNKNOWN'")

        query = f"SELECT * FROM message_audit_events WHERE {' AND '.join(where_clauses)} ORDER BY id DESC LIMIT 1"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def get_recent_failures(self, limit: int = 5, production_only: bool = True) -> List[Dict[str, Any]]:
        """Returns the most recent failed events for quick diagnostic display."""
        if self.is_supabase_primary:
            return self.supabase_repo.get_recent_failures(limit=limit, production_only=production_only)

        where_clauses = ["(event_type = 'FAILED' OR status = 'FAILED')"]
        params: List[Any] = []
        if production_only:
            where_clauses.append("is_test = 0")
        params.append(limit)

        query = f"SELECT * FROM message_audit_events WHERE {' AND '.join(where_clauses)} ORDER BY id DESC LIMIT ?"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            failures = []
            for r in rows:
                d = self._row_to_dict(r)
                det = d.get("details", {})
                failures.append({
                    "correlation_id": d.get("correlation_id"),
                    "phone": d.get("customer_phone"),
                    "timestamp": d.get("created_at"),
                    "code": det.get("error_code") if isinstance(det, dict) else None,
                    "type": det.get("error_type") if isinstance(det, dict) else None,
                    "error_message": det.get("error_message") if isinstance(det, dict) else None,
                })
            return failures

    def get_metrics_summary(self, hours: int = 24, production_only: bool = True) -> Dict[str, Any]:
        """
        Calculates operational health and volume metrics from the audit store
        over a specified rolling window (default 24h).
        Accurately computes failure rate percentage when denominator > 0.
        Delegates to Supabase in production and SQLite in development/test.
        """
        if self.is_supabase_primary:
            return self.supabase_repo.get_metrics_summary(hours=hours, production_only=production_only)

        cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Filter by reporting window and production isolation
            where_clauses = ["created_at >= ?"]
            params: List[Any] = [cutoff_iso]
            if production_only:
                where_clauses.append("is_test = 0")

            where_str = f"WHERE {' AND '.join(where_clauses)}"

            # Volume breakdown by event_type in the reporting window
            cursor.execute(f"""
                SELECT event_type, COUNT(*) as count
                FROM message_audit_events
                {where_str}
                GROUP BY event_type
            """, tuple(params))
            counts_by_event = {row["event_type"]: row["count"] for row in cursor.fetchall()}

            # Breakdown counts
            total_received = counts_by_event.get("RECEIVED", 0)
            total_accepted = counts_by_event.get("META_ACCEPTED", 0)
            total_sent = total_accepted + counts_by_event.get("SENT", 0)
            total_delivered = counts_by_event.get("DELIVERED", 0)
            total_read = counts_by_event.get("READ", 0)
            total_failed = counts_by_event.get("FAILED", 0)

            # Failure Rate Calculation: failed / (sent + failed) * 100
            # Only calculated when outbound attempts denominator > 0
            outbound_attempts = total_sent + total_failed
            if outbound_attempts > 0:
                failure_rate_percent: Optional[float] = round((total_failed / outbound_attempts) * 100.0, 1)
            else:
                failure_rate_percent = None

            # Last inbound message (production only)
            last_in_where = ["direction = 'INBOUND'", "event_type = 'RECEIVED'"]
            last_in_params: List[Any] = []
            if production_only:
                last_in_where.append("is_test = 0")
                last_in_where.append("customer_phone != 'UNKNOWN'")
            cursor.execute(f"""
                SELECT created_at, customer_phone, details
                FROM message_audit_events
                WHERE {' AND '.join(last_in_where)}
                ORDER BY id DESC LIMIT 1
            """, tuple(last_in_params))
            last_inbound_row = cursor.fetchone()
            last_inbound = self._row_to_dict(last_inbound_row) if last_inbound_row else None

            # Last outbound message (production only)
            last_out_where = ["direction = 'OUTBOUND'", "event_type IN ('META_ACCEPTED', 'SENT')"]
            last_out_params: List[Any] = []
            if production_only:
                last_out_where.append("is_test = 0")
                last_out_where.append("customer_phone != 'UNKNOWN'")
            cursor.execute(f"""
                SELECT created_at, customer_phone, status, wamid, details
                FROM message_audit_events
                WHERE {' AND '.join(last_out_where)}
                ORDER BY id DESC LIMIT 1
            """, tuple(last_out_params))
            last_outbound_row = cursor.fetchone()
            last_outbound = self._row_to_dict(last_outbound_row) if last_outbound_row else None

            # Last failure (production only)
            last_fail_where = ["(event_type = 'FAILED' OR status = 'FAILED')"]
            last_fail_params: List[Any] = []
            if production_only:
                last_fail_where.append("is_test = 0")
            cursor.execute(f"""
                SELECT created_at, customer_phone, event_type, details
                FROM message_audit_events
                WHERE {' AND '.join(last_fail_where)}
                ORDER BY id DESC LIMIT 1
            """, tuple(last_fail_params))
            last_failure_row = cursor.fetchone()
            last_failure = self._row_to_dict(last_failure_row) if last_failure_row else None

            return {
                "total_received": total_received,
                "total_inbound": total_received,
                "total_sent": total_sent,
                "total_accepted": total_accepted,
                "total_delivered": total_delivered,
                "total_read": total_read,
                "total_failed": total_failed,
                "failure_rate_percent": failure_rate_percent,
                "hours_window": hours,
                "last_inbound": last_inbound,
                "last_outbound": last_outbound,
                "last_failure": last_failure,
            }

    @staticmethod
    def _sanitize_dict(data: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively scrubs secret keys from audit details."""
        forbidden_keys = {"authorization", "token", "access_token", "secret", "app_secret", "key", "api_key"}
        sanitized = {}
        for k, v in data.items():
            if any(fk in k.lower() for fk in forbidden_keys):
                sanitized[k] = "[REDACTED]"
            elif isinstance(v, dict):
                sanitized[k] = AuditService._sanitize_dict(v)
            else:
                sanitized[k] = v
        return sanitized

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        if "details" in d and isinstance(d["details"], str):
            try:
                d["details"] = json.loads(d["details"])
            except Exception:
                pass
        return d


# Singleton instance for production use
audit_service = AuditService()
