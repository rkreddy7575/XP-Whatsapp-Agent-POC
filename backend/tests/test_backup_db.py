"""
test_backup_db.py
Tests for automated database backup mechanism.
"""

import os
import sqlite3
import sys
import unittest

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from scripts.backup_db import perform_backup, rotate_backups, BACKUP_DIR
from services.order_service import DEFAULT_PROD_DB_PATH


class TestBackupMechanism(unittest.TestCase):
    def test_perform_backup_creates_valid_snapshot(self):
        backup_file = perform_backup(source_path=DEFAULT_PROD_DB_PATH, retention_count=5)
        self.assertTrue(os.path.exists(backup_file))
        self.assertTrue(backup_file.endswith(".db"))

        # Verify integrity and order preservation
        conn = sqlite3.connect(backup_file)
        c = conn.cursor()
        c.execute("PRAGMA integrity_check;")
        self.assertEqual(c.fetchone()[0].lower(), "ok")
        c.execute("SELECT count(*) FROM orders;")
        count = c.fetchone()[0]
        self.assertGreaterEqual(count, 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
