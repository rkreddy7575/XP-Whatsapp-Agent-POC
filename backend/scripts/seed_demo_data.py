"""
Demo Data Seeder & Cleaner
Generates isolated, clearly marked DEMO records for testing the end-to-end business demo:
Customer -> WhatsApp -> Product Search -> Image presentation -> Selection -> Quote -> Order.

Usage:
    python scripts/seed_demo_data.py --seed
    python scripts/seed_demo_data.py --clear
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from services.pricing_service import pricing_service
from services.supabase_repository import (
    SupabaseClient,
    SupabaseOrderRepository,
    SupabaseCustomerRepository,
    SupabaseConversationRepository,
    SupabaseMessageRepository,
    SupabaseEnquiryRepository,
    SupabaseQuoteRepository,
)

DEFAULT_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "app.db"))

def get_target_db_path(explicit_path=None) -> str:
    if explicit_path:
        return explicit_path
    if os.getenv("DATABASE_PATH"):
        return os.path.abspath(os.getenv("DATABASE_PATH"))
    if any("unittest" in str(arg).lower() or "pytest" in str(arg).lower() for arg in sys.argv):
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "test_app.db"))
    return DEFAULT_DB_PATH

DEMO_PHONE = "919876543210"
DEMO_ORDER_ID = "DEMO-ORD-20260920-0099"
DEMO_CONV_ID = f"DEMO-CONV-{DEMO_PHONE}"
DEMO_ENQ_NUM = "DEMO-ENQ-20260920-0001"
DEMO_QUO_NUM = "DEMO-QUO-20260920-0001"
DEMO_SKU = "GS-002"
DEMO_QTY = 100


def seed_demo_data(db_path: str = None):
    print("=" * 60)
    print("SEEDING ISOLATED DEMO DATA (Marked: DEMO DATA)")
    print("=" * 60)

    now_iso = datetime.utcnow().isoformat()

    # Compute real pricing from deterministic engine — NEVER hardcode prices
    quote = pricing_service.calculate_total(DEMO_SKU, DEMO_QTY)
    if not quote.available:
        print(f"[ERROR] Pricing not available for {DEMO_SKU} x {DEMO_QTY}. Cannot seed demo data.")
        return
    unit_price = quote.unit_price_excl_gst
    gst_pct = quote.gst_percentage
    subtotal = quote.total_price_excl_gst
    total_gst = quote.total_gst
    grand_total = quote.total_price_incl_gst
    unit_gst = quote.unit_gst
    unit_incl = quote.unit_price_incl_gst
    print(f"[PRICING] {DEMO_SKU} x {DEMO_QTY}: {unit_price}/unit + {gst_pct}% GST = {grand_total} total")

    # 1. Seed SQLite
    target_path = get_target_db_path(db_path)
    if os.path.exists(target_path):
        conn = sqlite3.connect(target_path)
        c = conn.cursor()

        # Clean any old demo order in SQLite
        c.execute("DELETE FROM order_items WHERE order_id = ?", (DEMO_ORDER_ID,))
        c.execute("DELETE FROM orders WHERE order_id = ?", (DEMO_ORDER_ID,))
        c.execute("DELETE FROM conversation_messages WHERE conversation_id = ? OR conversation_id IN (SELECT conversation_id FROM conversations WHERE customer_phone = ?)", (DEMO_CONV_ID, DEMO_PHONE))
        c.execute("DELETE FROM conversations WHERE conversation_id = ? OR customer_phone = ?", (DEMO_CONV_ID, DEMO_PHONE))

        # Insert Demo Order — pricing from deterministic engine
        c.execute("""
            INSERT INTO orders (
                order_id, customer_phone, customer_name, status, subtotal,
                gst_amount, grand_total, currency, created_at, updated_at,
                pricing_version, inventory_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            DEMO_ORDER_ID, DEMO_PHONE, "DEMO Customer (Infosys)", "CONFIRMED",
            subtotal, total_gst, grand_total, "INR", now_iso, now_iso, "2025", "CONFIRMED_DEMO"
        ))

        # Insert Demo Order Item — pricing from deterministic engine
        c.execute("""
            INSERT INTO order_items (
                order_id, sku, quantity, unit_price, gst_rate, gst_amount, line_total
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            DEMO_ORDER_ID, DEMO_SKU, DEMO_QTY, unit_price, gst_pct, total_gst, grand_total
        ))

        # Insert Demo Conversation
        c.execute("""
            INSERT INTO conversations (
                conversation_id, customer_phone, last_intent, current_product_candidates,
                selected_sku, selected_quantity, pending_quote, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            DEMO_CONV_ID, DEMO_PHONE, "CONFIRM_ORDER", "[]", DEMO_SKU, DEMO_QTY, None, now_iso, now_iso
        ))

        # Insert Demo Conversation Messages (Full Journey)
        messages = [
            ("INBOUND", f"I need {DEMO_QTY} gift sets around 500"),
            ("OUTBOUND", f"Found 3 matching products with real photos:\n1. Premium Gift Set (GS-001)\n2. Executive Diary Set ({DEMO_SKU})\n3. Luxury Pen & Mug (GS-003)"),
            ("INBOUND", "2"),
            ("OUTBOUND", f"OFFICIAL QUOTATION:\nProduct: {DEMO_SKU} ({DEMO_QTY} units)\nUnit Price: {unit_price:,.2f} + {gst_pct:.0f}% GST\nTotal: {grand_total:,.2f} incl. GST\nReply 'Confirm' to place your order."),
            ("INBOUND", "Confirm"),
            ("OUTBOUND", f"ORDER CONFIRMED! Order ID: {DEMO_ORDER_ID}\nThank you for choosing Mudhra Branding Solutions."),
        ]
        for direction, text in messages:
            c.execute("""
                INSERT INTO conversation_messages (conversation_id, direction, message_text, timestamp)
                VALUES (?, ?, ?, ?)
            """, (DEMO_CONV_ID, direction, text, now_iso))

        conn.commit()
        conn.close()
        print("[SQLITE] Seeded Demo Order, Items, and 6-step Conversation history successfully.")

    # 2. Seed Supabase if configured
    sb_client = SupabaseClient()
    if sb_client.is_configured:
        print("[SUPABASE] Supabase credentials detected. Seeding demo records...")
        order_repo = SupabaseOrderRepository(sb_client)
        enq_repo = SupabaseEnquiryRepository(sb_client)
        quote_repo = SupabaseQuoteRepository(sb_client)
        conv_repo = SupabaseConversationRepository(sb_client)
        msg_repo = SupabaseMessageRepository(sb_client)

        order_repo.create_order({
            "order_id": DEMO_ORDER_ID,
            "customer_phone": DEMO_PHONE,
            "customer_name": "DEMO Customer (Infosys)",
            "status": "CONFIRMED",
            "subtotal": subtotal,
            "gst_amount": total_gst,
            "grand_total": grand_total,
            "currency": "INR",
            "pricing_version": "2025",
            "inventory_status": "CONFIRMED_DEMO",
            "notes": "DEMO DATA",
        }, [{
            "sku": DEMO_SKU,
            "product_name": "Gift Sets - Executive Diary Set",
            "quantity": DEMO_QTY,
            "unit_price": unit_price,
            "gst_rate": gst_pct,
            "gst_amount": total_gst,
            "line_total": grand_total,
        }])

        enq_repo.create_enquiry({
            "enquiry_number": DEMO_ENQ_NUM,
            "customer_phone": DEMO_PHONE,
            "customer_name": "DEMO Customer (Infosys)",
            "status": "CONVERTED",
            "category": "Gift Sets",
            "sku": DEMO_SKU,
            "quantity": DEMO_QTY,
            "budget_per_unit": 500.0,
            "notes": "DEMO DATA: Annual Corporate Gifting",
        })

        quote_repo.create_quote({
            "quote_number": DEMO_QUO_NUM,
            "customer_phone": DEMO_PHONE,
            "sku": DEMO_SKU,
            "quantity": DEMO_QTY,
            "unit_price_excl_gst": unit_price,
            "gst_percentage": gst_pct,
            "unit_gst": unit_gst,
            "unit_price_incl_gst": unit_incl,
            "total_price_excl_gst": subtotal,
            "total_gst": total_gst,
            "total_price_incl_gst": grand_total,
            "status": "ACCEPTED",
        })

        print("[SUPABASE] Seeded Demo Order, Enquiry, and Quote records.")

    print("\n[COMPLETE] Demo data successfully initialized and isolated.")


def clear_demo_data(db_path: str = None):
    print("=" * 60)
    print("REMOVING ISOLATED DEMO DATA")
    print("=" * 60)

    # 1. Clear SQLite
    target_path = get_target_db_path(db_path)
    if os.path.exists(target_path):
        conn = sqlite3.connect(target_path)
        c = conn.cursor()
        c.execute("DELETE FROM order_items WHERE order_id LIKE 'DEMO-%'")
        c.execute("DELETE FROM orders WHERE order_id LIKE 'DEMO-%'")
        c.execute("DELETE FROM conversation_messages WHERE conversation_id LIKE 'DEMO-%' OR conversation_id IN (SELECT conversation_id FROM conversations WHERE customer_phone = ?)", (DEMO_PHONE,))
        c.execute("DELETE FROM conversations WHERE conversation_id LIKE 'DEMO-%' OR customer_phone = ?", (DEMO_PHONE,))
        conn.commit()
        conn.close()
        print("[SQLITE] Cleared all records starting with DEMO-.")

    # 2. Clear Supabase if configured
    sb_client = SupabaseClient()
    if sb_client.is_configured:
        print("[SUPABASE] Clearing demo records from Supabase...")
        sb_client.client.delete(f"{sb_client.url}/rest/v1/order_items?order_id=like.DEMO-*", headers=sb_client._headers())
        sb_client.client.delete(f"{sb_client.url}/rest/v1/orders?order_id=like.DEMO-*", headers=sb_client._headers())
        sb_client.client.delete(f"{sb_client.url}/rest/v1/enquiries?enquiry_number=like.DEMO-*", headers=sb_client._headers())
        sb_client.client.delete(f"{sb_client.url}/rest/v1/quotes?quote_number=like.DEMO-*", headers=sb_client._headers())
        sb_client.client.delete(f"{sb_client.url}/rest/v1/conversations?conversation_id=like.DEMO-*", headers=sb_client._headers())
        print("[SUPABASE] Cleared Supabase demo records.")

    print("\n[COMPLETE] All DEMO records cleanly removed. Real production data untouched.")


def main():
    parser = argparse.ArgumentParser(description="Seed or clear isolated demo data")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--seed", action="store_true", help="Seed isolated demo data")
    group.add_argument("--clear", action="store_true", help="Clear isolated demo data")
    args = parser.parse_args()

    if args.seed:
        seed_demo_data()
    elif args.clear:
        clear_demo_data()


if __name__ == "__main__":
    main()
