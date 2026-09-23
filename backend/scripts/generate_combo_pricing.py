#!/usr/bin/env python3
"""
Generate and synchronize authoritative Combo pricing rules for customer SKUs (XG-501..XG-593).

AUTHORITATIVE SOURCE OF TRUTH:
- ALL combos price list .xlsx (Combos/Gift Sets, 2025 revision)
- 2024 archive files (e.g. PRICELIST-07.08.2024...) MUST NOT be used for production pricing.
- Ambiguous / mismatched SKUs (such as 2-in-1 rows mapped to 6-in-1 or 7-in-1 sets, or slash-prices)
  are left UNCONFIGURED (unavailable) rather than guessing or falling back.
"""

import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from services.supabase_repository import SupabaseClient

UNRESOLVED_COMBO_NUMS = {577, 586, 587, 588, 589, 590, 591, 592, 593}


def main():
    master_path = os.path.join(os.path.dirname(__file__), "..", "data", "pricing_master.json")
    master_path = os.path.abspath(master_path)

    if not os.path.exists(master_path):
        print("ERROR: pricing_master.json not found at:", master_path)
        sys.exit(1)

    with open(master_path, "r", encoding="utf-8") as f:
        master_data = json.load(f)

    # 1. Clean out any 2024 PRICELIST archive entries
    clean_master = [
        r for r in master_data
        if "PRICELIST" not in r.get("source_file", "")
    ]

    # Also remove any existing customer XG-5xx rules to rebuild cleanly
    clean_master = [
        r for r in clean_master
        if not (r.get("sku", "").startswith("XG-5") and r.get("source_file") == "ALL combos price list .xlsx")
    ]

    # 2. Find all authoritative 2025 combo records from ALL combos price list .xlsx
    authoritative_combos = [
        r for r in clean_master
        if r.get("source_file") == "ALL combos price list .xlsx"
    ]
    print("Found {} authoritative 2025 combo workbook records".format(len(authoritative_combos)))

    combos_by_num = {}
    for r in authoritative_combos:
        m = re.search(r"(\d+)$", r["sku"].strip())
        if m:
            combos_by_num[int(m.group(1))] = r

    generated_rules = []
    skipped_unresolved = []
    now_iso = datetime.utcnow().isoformat()

    for i in range(501, 594):
        sku = "XG-{}".format(i)
        if i in UNRESOLVED_COMBO_NUMS:
            skipped_unresolved.append(sku)
            continue
        auth_rec = combos_by_num.get(i)
        if not auth_rec:
            skipped_unresolved.append(sku)
            continue

        rule = {
            "sku": sku,
            "normalized_sku": sku,
            "category": "Combos/Gift Sets",
            "unit_price_excl_gst": auth_rec["unit_price_excl_gst"],
            "gst_percentage": 18.0,
            "quantity_from": 1,
            "quantity_to": None,
            "source_file": "ALL combos price list .xlsx",
            "source_sheet": auth_rec["source_sheet"],
            "source_row": auth_rec["source_row"],
            "source_category": auth_rec.get("source_category", "Combos/Gift Sets"),
            "imported_at": now_iso,
            "pricing_version": "2025",
        }
        generated_rules.append(rule)

    print("\n=== GENERATION SUMMARY ===")
    print("  Authoritative rules generated: {}".format(len(generated_rules)))
    print("  Unresolved/unavailable SKUs: {}".format(len(skipped_unresolved)))
    print("    List: {}".format(skipped_unresolved))

    # Append to master and save
    final_master = clean_master + generated_rules
    with open(master_path, "w", encoding="utf-8") as f:
        json.dump(final_master, f, indent=2, ensure_ascii=False)

    print("  Saved {} total records to pricing_master.json".format(len(final_master)))

    # Sync to Supabase
    sb = SupabaseClient()
    if sb.is_configured:
        print("\nSyncing to Supabase...")
        sb.delete("pricing_rules", {"source_file": "like.*PRICELIST*"})
        sb.delete("pricing_rules", {"sku": "like.XG-5*"})
        inserted = 0
        for r in generated_rules:
            row = dict(r)
            row.pop("imported_at", None)
            row["tenant_id"] = "default"
            row["status"] = "ACTIVE"
            if sb.insert("pricing_rules", row):
                inserted += 1
        print("  Synced {} / {} rules to Supabase pricing_rules".format(inserted, len(generated_rules)))
    else:
        print("  Supabase not configured; local pricing_master.json is updated.")


if __name__ == "__main__":
    main()
