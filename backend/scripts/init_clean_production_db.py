"""
init_clean_production_db.py
Initializes a clean production SQLite database from the POC archive.

Preserves strictly genuine live customer records:
- Order: ORD-20260920-0032 (Customer: 919553364395 / Ravikiran Reddy)
  SKU: GS-001, 100 units, Subtotal: ₹44,500, GST: ₹8,010, Grand Total: ₹52,510, Status: CONFIRMED
- Conversation: CONV-919553364395 (18 genuine customer-bot interaction messages)

Excludes all 106 automated test orders and mock conversation artifacts.
"""

import os
import shutil
import sqlite3
import sys

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
PROD_DB_PATH = os.path.join(DATA_DIR, "app.db")
ARCHIVE_DB_PATH = os.path.join(DATA_DIR, "app_poc_archive_20260920.db")
TEMP_CLEAN_DB_PATH = os.path.join(DATA_DIR, "app_clean_tmp.db")

GENUINE_ORDER_ID = "ORD-20260920-0032"
GENUINE_CUSTOMER_PHONE = "919553364395"


def init_clean_production_db():
    print("=" * 60)
    print("MUDHRA B2B PLATFORM — PRODUCTION DATABASE INITIALIZER")
    print("=" * 60)

    # 1. Verify archive exists or archive current production DB
    if not os.path.exists(ARCHIVE_DB_PATH):
        if os.path.exists(PROD_DB_PATH):
            print(f"[ARCHIVE] Archiving {PROD_DB_PATH} -> {ARCHIVE_DB_PATH}...")
            shutil.copyfile(PROD_DB_PATH, ARCHIVE_DB_PATH)
        else:
            print(f"[ERROR] Neither archive {ARCHIVE_DB_PATH} nor {PROD_DB_PATH} exists.")
            sys.exit(1)
    else:
        print(f"[ARCHIVE] Verified existing archive at {ARCHIVE_DB_PATH}")

    # 2. Connect to archive and extract genuine records
    print("[EXTRACT] Reading genuine customer records from archive...")
    conn_arch = sqlite3.connect(ARCHIVE_DB_PATH)
    conn_arch.row_factory = sqlite3.Row
    c_arch = conn_arch.cursor()

    c_arch.execute("SELECT * FROM orders WHERE order_id = ?", (GENUINE_ORDER_ID,))
    genuine_order = c_arch.fetchone()
    if not genuine_order:
        print(f"[ERROR] Genuine order {GENUINE_ORDER_ID} not found in archive!")
        sys.exit(1)

    c_arch.execute("SELECT * FROM order_items WHERE order_id = ?", (GENUINE_ORDER_ID,))
    genuine_items = c_arch.fetchall()

    c_arch.execute("SELECT * FROM conversations WHERE customer_phone = ?", (GENUINE_CUSTOMER_PHONE,))
    genuine_conv = c_arch.fetchone()

    genuine_messages = []
    if genuine_conv:
        conv_id = genuine_conv["conversation_id"]
        c_arch.execute(
            "SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY id ASC",
            (conv_id,),
        )
        genuine_messages = c_arch.fetchall()

    if sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print(f"[EXTRACT] Found genuine order: {genuine_order['order_id']} "
          f"({genuine_order['customer_name']}, Total: INR {genuine_order['grand_total']:,.2f}, Status: {genuine_order['status']})")
    print(f"[EXTRACT] Found {len(genuine_items)} order item(s).")
    print(f"[EXTRACT] Found genuine conversation: {genuine_conv['conversation_id'] if genuine_conv else 'None'} "
          f"with {len(genuine_messages)} messages.")

    conn_arch.close()

    # 3. Create fresh clean database file
    if os.path.exists(TEMP_CLEAN_DB_PATH):
        os.remove(TEMP_CLEAN_DB_PATH)

    print(f"[BUILD] Building clean database schema at {TEMP_CLEAN_DB_PATH}...")
    conn_new = sqlite3.connect(TEMP_CLEAN_DB_PATH)
    c_new = conn_new.cursor()

    # Apply SQLite performance & concurrency PRAGMAs
    c_new.execute("PRAGMA journal_mode=WAL;")
    c_new.execute("PRAGMA busy_timeout=5000;")

    # Orders schema
    c_new.execute("""
        CREATE TABLE orders (
            order_id TEXT PRIMARY KEY,
            customer_phone TEXT NOT NULL,
            customer_name TEXT,
            status TEXT NOT NULL,
            subtotal REAL NOT NULL,
            gst_amount REAL NOT NULL,
            grand_total REAL NOT NULL,
            currency TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            pricing_version TEXT NOT NULL,
            inventory_status TEXT NOT NULL
        );
    """)

    # Order items schema
    c_new.execute("""
        CREATE TABLE order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            sku TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            gst_rate REAL NOT NULL,
            gst_amount REAL NOT NULL,
            line_total REAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders (order_id)
        );
    """)

    # Conversations schema
    c_new.execute("""
        CREATE TABLE conversations (
            conversation_id TEXT PRIMARY KEY,
            customer_phone TEXT UNIQUE NOT NULL,
            last_intent TEXT,
            current_product_candidates TEXT,
            selected_sku TEXT,
            selected_quantity INTEGER,
            pending_quote TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)

    # Conversation messages schema
    c_new.execute("""
        CREATE TABLE conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            message_text TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations (conversation_id)
        );
    """)

    # 4. Insert genuine order
    print(f"[INSERT] Inserting genuine order {GENUINE_ORDER_ID}...")
    order_cols = list(genuine_order.keys())
    placeholders = ", ".join(["?"] * len(order_cols))
    col_names = ", ".join(order_cols)
    c_new.execute(
        f"INSERT INTO orders ({col_names}) VALUES ({placeholders})",
        [genuine_order[c] for c in order_cols],
    )

    # 5. Insert genuine items
    for itm in genuine_items:
        # Exclude original 'id' to preserve clean autoincrement
        c_new.execute("""
            INSERT INTO order_items (order_id, sku, quantity, unit_price, gst_rate, gst_amount, line_total)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            itm["order_id"],
            itm["sku"],
            itm["quantity"],
            itm["unit_price"],
            itm["gst_rate"],
            itm["gst_amount"],
            itm["line_total"],
        ))

    # 6. Insert genuine conversation
    if genuine_conv:
        conv_cols = list(genuine_conv.keys())
        conv_placeholders = ", ".join(["?"] * len(conv_cols))
        conv_col_names = ", ".join(conv_cols)
        c_new.execute(
            f"INSERT INTO conversations ({conv_col_names}) VALUES ({conv_placeholders})",
            [genuine_conv[c] for c in conv_cols],
        )

        for msg in genuine_messages:
            c_new.execute("""
                INSERT INTO conversation_messages (conversation_id, direction, message_text, timestamp)
                VALUES (?, ?, ?, ?)
            """, (
                msg["conversation_id"],
                msg["direction"],
                msg["message_text"],
                msg["timestamp"],
            ))

    conn_new.commit()

    # 7. Verification of clean DB
    c_new.execute("SELECT COUNT(*) FROM orders")
    total_orders = c_new.fetchone()[0]
    c_new.execute("SELECT COUNT(*) FROM order_items")
    total_items = c_new.fetchone()[0]
    c_new.execute("SELECT COUNT(*) FROM conversations")
    total_convs = c_new.fetchone()[0]
    c_new.execute("SELECT COUNT(*) FROM conversation_messages")
    total_msgs = c_new.fetchone()[0]

    conn_new.close()

    print(f"[VERIFY] Clean DB counts: {total_orders} order(s), {total_items} item(s), "
          f"{total_convs} conversation(s), {total_msgs} message(s).")

    if total_orders != 1 or total_items < 1:
        print("[ERROR] Clean database verification failed!")
        sys.exit(1)

    # 8. Atomically replace production app.db
    print(f"[SWAP] Replacing {PROD_DB_PATH} with clean database...")
    # Clean up WAL/SHM files if any
    for ext in ["-wal", "-shm"]:
        wal_file = PROD_DB_PATH + ext
        if os.path.exists(wal_file):
            try:
                os.remove(wal_file)
            except Exception:
                pass

    shutil.move(TEMP_CLEAN_DB_PATH, PROD_DB_PATH)
    print("=" * 60)
    print("CLEAN PRODUCTION DATABASE INITIALIZATION SUCCESSFUL!")
    print(f"Production DB: {PROD_DB_PATH}")
    print(f"Archive DB:    {ARCHIVE_DB_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    init_clean_production_db()
