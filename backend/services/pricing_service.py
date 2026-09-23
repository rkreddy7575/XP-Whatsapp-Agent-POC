import sys
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

@dataclass
class PriceRule:
    """Represents a quantity-tier pricing rule for an SKU."""
    sku: str
    quantity_from: int
    quantity_to: Optional[int]  # None indicates open-ended (e.g., 250+)
    base_price: float           # Price per unit excluding GST
    gst_percentage: float = 18.0
    source: str = "LOCAL_EXCEL_2025"
    source_file: Optional[str] = None
    source_sheet: Optional[str] = None
    source_row: Optional[int] = None
    source_category: Optional[str] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    pricing_version: Optional[str] = "2025"

@dataclass
class PriceQuoteResult:
    """Structured result of a pricing calculation."""
    available: bool
    sku: str
    quantity: int
    unit_price_excl_gst: Optional[float] = None
    gst_percentage: Optional[float] = None
    unit_gst: Optional[float] = None
    unit_price_incl_gst: Optional[float] = None
    total_price_excl_gst: Optional[float] = None
    total_gst: Optional[float] = None
    total_price_incl_gst: Optional[float] = None
    source: Optional[str] = None
    source_file: Optional[str] = None
    source_sheet: Optional[str] = None
    source_row: Optional[int] = None
    pricing_version: Optional[str] = None
    bracket_from: Optional[int] = None
    bracket_to: Optional[int] = None
    message: Optional[str] = None


class PricingService:
    """
    Deterministic Pricing Engine.
    Evaluates tiered volume pricing, GST, and totals based strictly on configured business rules.
    Never guesses or invents prices.
    """
    def __init__(self, load_master: bool = False, master_file: Optional[str] = None):
        # Maps normalized alphanumeric SKU -> list of PriceRule sorted by quantity_from
        self._rules: Dict[str, List[PriceRule]] = {}
        if load_master:
            self.load_pricing_master(master_file)

    @staticmethod
    def _normalize_key(sku: str) -> str:
        """Strips whitespace and punctuation for consistent lookup matching."""
        if not sku:
            return ""
        return re.sub(r'[^A-Z0-9]', '', sku.upper())

    def clear_rules(self) -> None:
        """Clear all active pricing rules (used for testing or reloading)."""
        self._rules.clear()

    def add_rule(self, rule: PriceRule) -> None:
        """Add a single tiered pricing rule for an SKU."""
        clean_key = self._normalize_key(rule.sku)
        if not clean_key:
            return
        if clean_key not in self._rules:
            self._rules[clean_key] = []
        self._rules[clean_key].append(rule)
        # Keep brackets sorted by quantity_from ascending
        self._rules[clean_key].sort(key=lambda r: r.quantity_from)

    def load_rules(self, rules: List[PriceRule]) -> None:
        """Bulk load pricing rules."""
        for rule in rules:
            self.add_rule(rule)

    def load_pricing_master(self, file_path: Optional[str] = None) -> int:
        """
        Loads normalized pricing rules from pricing_master.json.
        Returns the number of rules loaded.
        """
        if file_path is None:
            file_path = os.path.join(os.path.dirname(__file__), "..", "data", "pricing_master.json")

        file_path = os.path.abspath(file_path)
        if not os.path.exists(file_path):
            return 0

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        rules: List[PriceRule] = []
        for item in data:
            rules.append(PriceRule(
                sku=item["sku"],
                quantity_from=item["quantity_from"],
                quantity_to=item.get("quantity_to"),
                base_price=float(item["unit_price_excl_gst"]),
                gst_percentage=float(item.get("gst_percentage", 18.0)),
                source=item.get("source_file", "LOCAL_EXCEL_2025"),
                source_file=item.get("source_file"),
                source_sheet=item.get("source_sheet"),
                source_row=item.get("source_row"),
                source_category=item.get("source_category"),
                pricing_version=item.get("pricing_version", "2025"),
            ))

        self.load_rules(rules)
        return len(rules)

    @staticmethod
    def calculate_gst(price: float, gst_rate: float) -> float:
        """Calculates GST amount rounded to 2 decimal places."""
        if price <= 0 or gst_rate <= 0:
            return 0.0
        return round(price * (gst_rate / 100.0), 2)

    def _lookup_rules(self, clean_key: str) -> List[PriceRule]:
        """Looks up rules for clean_key, falling back to XG prefix and cross-category alias variations."""
        rules = self._rules.get(clean_key, [])
        if not rules:
            if not clean_key.startswith("XG"):
                rules = self._rules.get(f"XG{clean_key}", [])
            elif clean_key.startswith("XG"):
                rules = self._rules.get(clean_key[2:], [])

        # Cross-category alias: XG-EL-xxx <-> XG-T-xxx (Electronics / Technology)
        if not rules:
            if "EL" in clean_key:
                t_key = clean_key.replace("EL", "T")
                rules = self._rules.get(t_key, [])
                if not rules and not t_key.startswith("XG"):
                    rules = self._rules.get(f"XG{t_key}", [])
                elif not rules and t_key.startswith("XG"):
                    rules = self._rules.get(t_key[2:], [])
            elif "T" in clean_key:
                el_key = clean_key.replace("T", "EL")
                rules = self._rules.get(el_key, [])
                if not rules and not el_key.startswith("XG"):
                    rules = self._rules.get(f"XG{el_key}", [])
                elif not rules and el_key.startswith("XG"):
                    rules = self._rules.get(el_key[2:], [])

        return rules

    def get_any_rule_for_sku(self, sku: str) -> Optional[PriceRule]:
        """Returns the first configured PriceRule for this SKU if any exists."""
        clean_key = self._normalize_key(sku)
        rules = self._lookup_rules(clean_key)
        return rules[0] if rules else None

    def get_price_for_quantity(self, sku: str, quantity: int) -> Optional[PriceRule]:
        """
        Finds the exact pricing rule matching the SKU and quantity bracket.
        Returns None if no matching rule is configured.
        """
        if not sku or quantity <= 0:
            return None

        clean_key = self._normalize_key(sku)
        rules = self._lookup_rules(clean_key)

        for rule in rules:
            if rule.quantity_from <= quantity:
                if rule.quantity_to is None or quantity <= rule.quantity_to:
                    return rule

        return None

    def calculate_total(self, sku: str, quantity: int) -> PriceQuoteResult:
        """
        Calculates unit pricing, GST, and totals for a given SKU and quantity.
        If no rule exists for this SKU or quantity, returns a safe 'unavailable' result.
        """
        clean_sku = sku.strip().upper() if sku else ""
        if not clean_sku or quantity <= 0:
            return PriceQuoteResult(
                available=False,
                sku=clean_sku,
                quantity=quantity,
                message="Invalid request: Please specify a valid product code and positive quantity."
            )

        # Supabase-first pricing lookup if configured
        if not any("unittest" in str(arg).lower() or "pytest" in str(arg).lower() for arg in sys.argv) or os.getenv("USE_SUPABASE_IN_TESTS") in ("1", "true", "True"):
            try:
                from services.supabase_repository import SupabaseClient, SupabasePricingRepository
                sb = SupabaseClient()
                if sb.is_configured:
                    sb_quote = SupabasePricingRepository(sb).calculate_price(clean_sku, quantity)
                    if sb_quote and sb_quote.available:
                        sb_quote.sku = clean_sku
                        return sb_quote
            except Exception:
                pass

        rule = self.get_price_for_quantity(clean_sku, quantity)
        if not rule:
            return PriceQuoteResult(
                available=False,
                sku=clean_sku,
                quantity=quantity,
                message=f"Pricing for *{clean_sku}* is not configured yet. Please contact sales."
            )

        unit_excl = round(float(rule.base_price), 2)
        unit_gst = self.calculate_gst(unit_excl, rule.gst_percentage)
        unit_incl = round(unit_excl + unit_gst, 2)

        total_excl = round(unit_excl * quantity, 2)
        total_gst = round(unit_gst * quantity, 2)
        total_incl = round(total_excl + total_gst, 2)

        # Preserve clean canonical display SKU
        if clean_sku:
            display_sku = clean_sku
        elif rule.sku:
            display_sku = re.sub(r'\s+', ' ', rule.sku).strip()
        else:
            display_sku = ""

        return PriceQuoteResult(
            available=True,
            sku=display_sku,
            quantity=quantity,
            unit_price_excl_gst=unit_excl,
            gst_percentage=rule.gst_percentage,
            unit_gst=unit_gst,
            unit_price_incl_gst=unit_incl,
            total_price_excl_gst=total_excl,
            total_gst=total_gst,
            total_price_incl_gst=total_incl,
            source=rule.source,
            source_file=rule.source_file,
            source_sheet=rule.source_sheet,
            source_row=rule.source_row,
            pricing_version=rule.pricing_version,
            bracket_from=rule.quantity_from,
            bracket_to=rule.quantity_to,
            message="Quote calculated successfully."
        )

    def format_quotation(
        self,
        quote: PriceQuoteResult,
        stock_status: Optional[str] = None,
        product_name: Optional[str] = None,
    ) -> str:
        """Formats a quote result into clean, customer-friendly WhatsApp markdown."""
        if not quote.available:
            return f"Pricing for {quote.sku} is not configured yet. Please contact sales."

        bracket_desc = (
            f"{quote.bracket_from}+ units"
            if quote.bracket_to is None
            else f"{quote.bracket_from}–{quote.bracket_to} units"
        )

        title_suffix = f" — _{product_name}_" if product_name else ""

        lines = [
            f"📋 *Quotation for {quote.sku}*{title_suffix}",
            f"🔢 *Quantity:* {quote.quantity} units (Tier: {bracket_desc})\n",
            f"💵 *Unit Price:* ₹{quote.unit_price_excl_gst:,.2f} *(excl. GST)*",
            f"🏷️ *GST ({quote.gst_percentage}%):* ₹{quote.unit_gst:,.2f} / unit",
            f"💳 *Unit Price (incl. GST):* ₹{quote.unit_price_incl_gst:,.2f}\n",
            f"🧾 *Subtotal:* ₹{quote.total_price_excl_gst:,.2f}",
            f"📊 *Total GST ({quote.gst_percentage}%):* ₹{quote.total_gst:,.2f}",
            f"💰 *Grand Total:* ₹{quote.total_price_incl_gst:,.2f} *(incl. GST)*",
        ]

        if stock_status:
            lines.append(f"\n{stock_status}")

        lines.append("\nℹ️ _Price shown is based on the current catalogue pricing._")
        return "\n".join(lines)

    @staticmethod
    def extract_sku_and_quantity(text: str, sku_resolver=None) -> Optional[Tuple[str, int]]:
        """
        Extracts an SKU candidate and an integer quantity from customer text.
        Supports inputs like:
        - 'XG-GS-501 100'
        - '100 XG-GS-501'
        - 'XG-MP 01 100'
        - '100 XG-MP 01'
        - 'xg-501 qty 50'
        - 'price for XG-501 250'
        """
        if not text:
            return None

        clean_text = text.strip()

        # 1. Check for pen pattern like 'XG-MP 01' or 'XG - MP 01' or 'XG-MP-01'
        pen_match = re.search(r'\b(XG\s*-\s*MP[\s-]*\d+)\b', clean_text, re.IGNORECASE)
        if pen_match:
            sku = re.sub(r'\s+', ' ', pen_match.group(1)).strip().upper()
            rem_text = clean_text[:pen_match.start()] + " " + clean_text[pen_match.end():]
            nums = re.findall(r'\b\d+\b', rem_text)
            if nums:
                qty = int(nums[0])
                if 1 <= qty <= 100000:
                    return sku, qty

        # 2. Standard token pattern: SKU token + quantity token
        tokens = re.findall(r'[a-zA-Z0-9\-]+', clean_text)
        if len(tokens) < 2:
            return None

        qty = None
        sku_candidate = None

        for t in tokens:
            if t.isdigit():
                val = int(t)
                if 1 <= val <= 100000 and qty is None:
                    qty = val
            else:
                upper_t = t.upper()
                # Check if candidate matches known SKU patterns or prefixes
                if any(upper_t.startswith(prefix) for prefix in ["XG-", "GS-", "XG", "GS", "K-", "F1", "C1"]):
                    sku_candidate = t
                elif sku_resolver:
                    resolved = sku_resolver(t)
                    if resolved:
                        sku_candidate = resolved.get("sku")

        if sku_candidate and qty:
            if sku_resolver:
                resolved = sku_resolver(sku_candidate)
                if resolved and resolved.get("sku"):
                    return resolved["sku"], qty
            return sku_candidate.strip().upper(), qty

        return None


# Singleton instance for production use
pricing_service = PricingService()
# Load production pricing master from backend/data/pricing_master.json if available
pricing_service.load_pricing_master()
