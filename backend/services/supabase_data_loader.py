"""
Supabase Data Loader & Migration Generator
Reuses existing catalogue_master.json (768 products) and pricing_master.json (861 pricing records).
Provides deterministic normalization, validation, duplicate prevention, and SQL seed generation.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from services.supabase_models import (
    ProductRecord,
    PricingRuleRecord,
    normalize_sku,
    clean_sku_key,
)

DEFAULT_CATALOGUE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "catalogue_master.json")
)
DEFAULT_PRICING_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "pricing_master.json")
)


class SupabaseDataLoader:
    """
    Handles parsing, normalization, validation, and SQL generation
    for migrating Mudhra catalogue and pricing data to Supabase.
    """

    def __init__(
        self,
        catalogue_path: Optional[str] = None,
        pricing_path: Optional[str] = None,
        tenant_id: str = "default",
    ):
        self.catalogue_path = catalogue_path or DEFAULT_CATALOGUE_PATH
        self.pricing_path = pricing_path or DEFAULT_PRICING_PATH
        self.tenant_id = tenant_id

    def load_catalogue(self) -> List[ProductRecord]:
        """
        Loads and validates all 768 products from catalogue_master.json.
        Enforces unique SKUs and preserves source metadata.
        """
        if not os.path.exists(self.catalogue_path):
            raise FileNotFoundError(f"Catalogue file not found at: {self.catalogue_path}")

        with open(self.catalogue_path, "r", encoding="utf-8") as f:
            raw_products = json.load(f)

        products: List[ProductRecord] = []
        seen_skus: Set[str] = set()
        duplicates: List[str] = []

        for idx, item in enumerate(raw_products):
            raw_sku = item.get("sku")
            if not raw_sku:
                continue

            sku = normalize_sku(raw_sku)
            if sku in seen_skus:
                duplicates.append(sku)
                continue
            seen_skus.add(sku)

            source = item.get("source") or {}
            prod = ProductRecord(
                sku=sku,
                normalized_sku=sku,
                category=item.get("category", "General"),
                tenant_id=self.tenant_id,
                name=item.get("name"),
                subcategory=item.get("subcategory"),
                description=item.get("description"),
                components=item.get("components") or [],
                colors=item.get("colors") or [],
                moq=item.get("moq"),
                status=item.get("status", "ACTIVE"),
                source_file=source.get("file"),
                source_sheet=source.get("sheet"),
                source_row=source.get("row"),
                source_price=str(item.get("source_price")) if item.get("source_price") is not None else None,
                metadata={"imported_from": "catalogue_master.json", "index": idx},
            )
            products.append(prod)

        return products

    def load_pricing(self) -> List[PricingRuleRecord]:
        """
        Loads and validates all 861 pricing rules from pricing_master.json.
        Preserves quantity-tier brackets, GST rates, and pricing version.
        Enforces bracket uniqueness per SKU.
        """
        if not os.path.exists(self.pricing_path):
            raise FileNotFoundError(f"Pricing file not found at: {self.pricing_path}")

        with open(self.pricing_path, "r", encoding="utf-8") as f:
            raw_rules = json.load(f)

        rules: List[PricingRuleRecord] = []
        seen_brackets: Set[Tuple[str, int, Optional[int], str]] = set()
        duplicates: List[Tuple[str, int, Optional[int]]] = []

        for item in raw_rules:
            raw_sku = item.get("sku")
            if not raw_sku:
                continue

            sku = normalize_sku(raw_sku)
            q_from = int(item.get("quantity_from", 1))
            q_to = int(item["quantity_to"]) if item.get("quantity_to") is not None else None
            p_ver = item.get("pricing_version", "2025")

            bracket_key = (sku, q_from, q_to, p_ver)
            if bracket_key in seen_brackets:
                duplicates.append((sku, q_from, q_to))
                continue
            seen_brackets.add(bracket_key)

            price = float(item.get("unit_price_excl_gst", 0.0))
            gst = float(item.get("gst_percentage", 18.0))

            rule = PricingRuleRecord(
                sku=sku,
                normalized_sku=sku,
                unit_price_excl_gst=price,
                tenant_id=self.tenant_id,
                category=item.get("category"),
                quantity_from=q_from,
                quantity_to=q_to,
                gst_percentage=gst,
                source_file=item.get("source_file"),
                source_sheet=item.get("source_sheet"),
                source_row=item.get("source_row"),
                source_category=item.get("source_category"),
                pricing_version=p_ver,
                status="ACTIVE",
            )
            rules.append(rule)

        return rules

    def validate(self) -> Dict[str, Any]:
        """
        Performs thorough validation across both datasets.
        """
        products = self.load_catalogue()
        pricing = self.load_pricing()

        # Check pricing categories and GST
        gst_summary = {}
        for r in pricing:
            cat = r.category or "Unknown"
            if cat not in gst_summary:
                gst_summary[cat] = set()
            gst_summary[cat].add(r.gst_percentage)

        gst_summary_serializable = {k: sorted(list(v)) for k, v in gst_summary.items()}

        # Check tiered brackets
        tiered_rules = [r for r in pricing if r.quantity_to is not None or r.quantity_from > 1]

        # Catalogue SKUs vs Pricing SKUs matching
        cat_skus = {p.sku for p in products}
        pri_skus = {r.sku for r in pricing}
        direct_matches = cat_skus.intersection(pri_skus)

        # Clean alphanumeric matches
        cat_clean = {clean_sku_key(s): s for s in cat_skus}
        pri_clean = {clean_sku_key(s): s for s in pri_skus}
        clean_matches = set(cat_clean.keys()).intersection(set(pri_clean.keys()))

        return {
            "catalogue_products_ready": len(products),
            "catalogue_unique_skus": len(cat_skus),
            "pricing_records_ready": len(pricing),
            "pricing_unique_skus": len(pri_skus),
            "tiered_pricing_rules": len(tiered_rules),
            "direct_sku_matches": len(direct_matches),
            "normalized_sku_matches": len(clean_matches),
            "gst_rates_by_category": gst_summary_serializable,
            "all_product_prices_positive": all(r.unit_price_excl_gst > 0 for r in pricing),
            "all_quantities_valid": all(r.quantity_from >= 1 for r in pricing),
        }

    def generate_sql_seed(self, output_path: str) -> None:
        """
        Generates an idempotent SQL seed file with batch INSERT ... ON CONFLICT
        statements for PostgreSQL / Supabase.
        """
        products = self.load_catalogue()
        pricing = self.load_pricing()

        def escape_sql(val: Any) -> str:
            if val is None:
                return "NULL"
            if isinstance(val, (int, float)):
                return str(val)
            if isinstance(val, bool):
                return "TRUE" if val else "FALSE"
            if isinstance(val, (dict, list)):
                escaped_json = json.dumps(val).replace("'", "''")
                return f"'{escaped_json}'::jsonb"
            escaped_str = str(val).replace("'", "''")
            return f"'{escaped_str}'"

        lines: List[str] = [
            "-- =============================================================================",
            "-- Migration: 002_seed_catalogue_and_pricing.sql",
            f"-- Generated: 768 Products and 861 Pricing Rules",
            f"-- Tenant: {self.tenant_id}",
            "-- Idempotent seed script with ON CONFLICT DO UPDATE",
            "-- =============================================================================",
            "",
            "BEGIN;",
            "",
            "-- -----------------------------------------------------------------------------",
            "-- 1. SEED PRODUCTS (768 records)",
            "-- -----------------------------------------------------------------------------",
        ]

        # Insert products in batches of 100
        batch_size = 100
        for i in range(0, len(products), batch_size):
            batch = products[i : i + batch_size]
            lines.append("INSERT INTO products (")
            lines.append("    tenant_id, sku, normalized_sku, name, category, subcategory,")
            lines.append("    description, components, colors, moq, status, source_file,")
            lines.append("    source_sheet, source_row, source_price, metadata")
            lines.append(") VALUES")
            val_rows = []
            for p in batch:
                row = (
                    f"    ({escape_sql(p.tenant_id)}, {escape_sql(p.sku)}, {escape_sql(p.normalized_sku)}, "
                    f"{escape_sql(p.name)}, {escape_sql(p.category)}, {escape_sql(p.subcategory)}, "
                    f"{escape_sql(p.description)}, {escape_sql(p.components)}, {escape_sql(p.colors)}, "
                    f"{escape_sql(p.moq)}, {escape_sql(p.status)}, {escape_sql(p.source_file)}, "
                    f"{escape_sql(p.source_sheet)}, {escape_sql(p.source_row)}, {escape_sql(p.source_price)}, "
                    f"{escape_sql(p.metadata)})"
                )
                val_rows.append(row)
            lines.append(",\n".join(val_rows))
            lines.append("ON CONFLICT (tenant_id, sku) DO UPDATE SET")
            lines.append("    category = EXCLUDED.category,")
            lines.append("    subcategory = EXCLUDED.subcategory,")
            lines.append("    description = EXCLUDED.description,")
            lines.append("    components = EXCLUDED.components,")
            lines.append("    colors = EXCLUDED.colors,")
            lines.append("    moq = EXCLUDED.moq,")
            lines.append("    source_price = EXCLUDED.source_price,")
            lines.append("    updated_at = TIMEZONE('utc'::text, NOW());")
            lines.append("")

        lines.append("-- -----------------------------------------------------------------------------")
        lines.append("-- 2. SEED PRICING RULES (861 records)")
        lines.append("-- -----------------------------------------------------------------------------")

        for i in range(0, len(pricing), batch_size):
            batch = pricing[i : i + batch_size]
            lines.append("INSERT INTO pricing_rules (")
            lines.append("    tenant_id, sku, normalized_sku, category, quantity_from, quantity_to,")
            lines.append("    unit_price_excl_gst, gst_percentage, source_file, source_sheet,")
            lines.append("    source_row, source_category, pricing_version, status")
            lines.append(") VALUES")
            val_rows = []
            for r in batch:
                row = (
                    f"    ({escape_sql(r.tenant_id)}, {escape_sql(r.sku)}, {escape_sql(r.normalized_sku)}, "
                    f"{escape_sql(r.category)}, {escape_sql(r.quantity_from)}, {escape_sql(r.quantity_to)}, "
                    f"{escape_sql(r.unit_price_excl_gst)}, {escape_sql(r.gst_percentage)}, {escape_sql(r.source_file)}, "
                    f"{escape_sql(r.source_sheet)}, {escape_sql(r.source_row)}, {escape_sql(r.source_category)}, "
                    f"{escape_sql(r.pricing_version)}, {escape_sql(r.status)})"
                )
                val_rows.append(row)
            lines.append(",\n".join(val_rows))
            lines.append("ON CONFLICT (tenant_id, normalized_sku, quantity_from, COALESCE(quantity_to, -1), pricing_version) DO UPDATE SET")
            lines.append("    unit_price_excl_gst = EXCLUDED.unit_price_excl_gst,")
            lines.append("    gst_percentage = EXCLUDED.gst_percentage,")
            lines.append("    status = EXCLUDED.status,")
            lines.append("    updated_at = TIMEZONE('utc'::text, NOW());")
            lines.append("")

        lines.append("COMMIT;")
        lines.append("")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
