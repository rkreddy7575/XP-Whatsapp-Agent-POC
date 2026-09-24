"""
Audit Service for WhatsApp Message Observability and Lifecycle Tracking.

Tracks the complete lifecycle of customer conversations:
  RECEIVED -> PERSISTED -> ROUTING_STARTED -> ROUTING_COMPLETED ->
  SEND_ATTEMPT -> META_ACCEPTED -> SENT -> DELIVERED -> READ (or FAILED)

Ensures zero secret leakage: token values and authorization headers are never recorded.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default SQLite database path
DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "app.db")


class AuditService:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.getenv("SQLITE_DB_PATH", DEFAULT_DB_PATH)
        self._ensure_tables()

    def _get_connection(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        """Initializes SQLite schema for message audit events."""
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
                        created_at TEXT NOT NULL
                    );
                """)
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
                conn.commit()
        except Exception as exc:
            logger.error("Failed to initialize message_audit_events table: %s", exc)

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
    ) -> None:
        """
        Records a granular lifecycle event in the audit store.
        Never stores secret credentials.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        raw_phone = phone if phone is not None else customer_phone
        clean_phone = raw_phone.lstrip("+").strip() if raw_phone else "UNKNOWN"
        safe_details = details or {}

        # Strip any accidental secrets from details dict defensively
        sanitized_details = self._sanitize_dict(safe_details)
        details_json = json.dumps(sanitized_details)

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO message_audit_events (
                        correlation_id, customer_phone, direction, event_type,
                        status, wamid, details, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
    ) -> None:
        """
        Records a Meta status update webhook (sent, delivered, read, failed).
        Updates conversation message status if corresponding record exists.
        """
        if not wamid:
            return

        target_phone = recipient_id or recipient
        now_iso = datetime.now(timezone.utc).isoformat()
        clean_phone = target_phone.lstrip("+").strip() if target_phone else ""
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

        # 1. Insert audit event
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Lookup phone or correlation_id if missing
                if not clean_phone:
                    cursor.execute(
                        "SELECT customer_phone FROM message_audit_events WHERE wamid = ? LIMIT 1",
                        (wamid,),
                    )
                    row = cursor.fetchone()
                    if row:
                        clean_phone = row["customer_phone"]

                correlation_id = f"status_{wamid}"
                cursor.execute(
                    """
                    INSERT INTO message_audit_events (
                        correlation_id, customer_phone, direction, event_type,
                        status, wamid, details, created_at
                    ) VALUES (?, ?, 'OUTBOUND', ?, ?, ?, ?, ?)
                    """,
                    (
                        correlation_id,
                        clean_phone or "UNKNOWN",
                        canonical_status,
                        canonical_status,
                        wamid,
                        json.dumps(details),
                        now_iso,
                    ),
                )

                # 2. Update status in conversation_messages if column exists
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

    def get_events_for_correlation(self, correlation_id: str) -> List[Dict[str, Any]]:
        """Returns chronological list of events for a specific message/turn correlation."""
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
    ) -> List[Dict[str, Any]]:
        """Returns recent audit events across the platform, optionally filtered."""
        clauses = []
        params = []
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

    def get_last_event_by_direction(self, direction: str) -> Optional[Dict[str, Any]]:
        """Returns the single most recent audit event for a given direction (INBOUND/OUTBOUND)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM message_audit_events WHERE direction = ? ORDER BY id DESC LIMIT 1",
                (direction.upper(),),
            )
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def get_recent_failures(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Returns the most recent failed events for quick diagnostic display."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM message_audit_events
                WHERE event_type = 'FAILED' OR status = 'FAILED'
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            )
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

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Calculates operational health and volume metrics from the audit store."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Volume breakdown
            cursor.execute("""
                SELECT event_type, COUNT(*) as count
                FROM message_audit_events
                GROUP BY event_type
            """)
            counts_by_event = {row["event_type"]: row["count"] for row in cursor.fetchall()}

            # Last inbound message
            cursor.execute("""
                SELECT created_at, customer_phone, details
                FROM message_audit_events
                WHERE direction = 'INBOUND' AND event_type = 'RECEIVED'
                ORDER BY id DESC LIMIT 1
            """)
            last_inbound_row = cursor.fetchone()
            last_inbound = self._row_to_dict(last_inbound_row) if last_inbound_row else None

            # Last outbound message
            cursor.execute("""
                SELECT created_at, customer_phone, status, wamid, details
                FROM message_audit_events
                WHERE direction = 'OUTBOUND' AND event_type IN ('META_ACCEPTED', 'SENT')
                ORDER BY id DESC LIMIT 1
            """)
            last_outbound_row = cursor.fetchone()
            last_outbound = self._row_to_dict(last_outbound_row) if last_outbound_row else None

            # Last failure
            cursor.execute("""
                SELECT created_at, customer_phone, event_type, details
                FROM message_audit_events
                WHERE event_type = 'FAILED' OR status = 'FAILED'
                ORDER BY id DESC LIMIT 1
            """)
            last_failure_row = cursor.fetchone()
            last_failure = self._row_to_dict(last_failure_row) if last_failure_row else None

            return {
                "total_inbound": counts_by_event.get("RECEIVED", 0),
                "total_accepted": counts_by_event.get("META_ACCEPTED", 0),
                "total_delivered": counts_by_event.get("DELIVERED", 0),
                "total_read": counts_by_event.get("READ", 0),
                "total_failed": counts_by_event.get("FAILED", 0),
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
