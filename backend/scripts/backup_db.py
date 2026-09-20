"""
backup_db.py
Automated atomic SQLite backup mechanism for Mudhra B2B platform.
Uses SQLite's online backup API (conn.backup) to produce point-in-time,
corruption-safe database snapshots without interrupting active webhook or API traffic.
"""

import os
import sqlite3
import sys
from datetime import datetime

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
PROD_DB_PATH = os.path.join(DATA_DIR, "app.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
MAX_BACKUPS_TO_RETAIN = 15


def perform_backup(source_path: str = PROD_DB_PATH, retention_count: int = MAX_BACKUPS_TO_RETAIN) -> str:
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"Source database not found: {source_path}")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"app_backup_{timestamp}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_filename)

    print(f"[BACKUP] Initiating online atomic backup...")
    print(f"[BACKUP] Source:      {source_path}")
    print(f"[BACKUP] Destination: {backup_path}")

    # Connect to source and destination
    src_conn = sqlite3.connect(source_path)
    dst_conn = sqlite3.connect(backup_path)

    try:
        # Perform online backup (safe with WAL mode and concurrent writers)
        src_conn.backup(dst_conn)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()

    # Integrity verification on backup file
    verify_conn = sqlite3.connect(backup_path)
    c = verify_conn.cursor()
    c.execute("PRAGMA integrity_check;")
    status = c.fetchone()[0]
    c.execute("SELECT count(*) FROM orders;")
    order_count = c.fetchone()[0]
    verify_conn.close()

    if status.lower() != "ok":
        raise RuntimeError(f"Integrity check failed on backup {backup_path}: {status}")

    file_size_kb = os.path.getsize(backup_path) / 1024
    print(f"[BACKUP] Verification OK (Integrity: {status}, Orders: {order_count}, Size: {file_size_kb:.1f} KB)")

    # Rotate old backups
    rotate_backups(retention_count)
    return backup_path


def rotate_backups(retention_count: int) -> None:
    if not os.path.exists(BACKUP_DIR):
        return

    backup_files = [
        os.path.join(BACKUP_DIR, f)
        for f in os.listdir(BACKUP_DIR)
        if f.startswith("app_backup_") and f.endswith(".db")
    ]
    backup_files.sort(key=os.path.getmtime, reverse=True)

    if len(backup_files) > retention_count:
        for stale in backup_files[retention_count:]:
            try:
                os.remove(stale)
                print(f"[ROTATE] Pruned older backup: {os.path.basename(stale)}")
            except Exception as exc:
                print(f"[WARNING] Failed to prune {stale}: {exc}")


if __name__ == "__main__":
    try:
        dest = perform_backup()
        print(f"[SUCCESS] Database backup completed: {dest}")
    except Exception as err:
        print(f"[ERROR] Backup failed: {err}", file=sys.stderr)
        sys.exit(1)
