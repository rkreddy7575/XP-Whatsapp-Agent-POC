import os
import csv
import io
import re
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from services.catalogue_service import catalogue_service
from services.inventory_provider import (
    AvailabilityResult,
    DevelopmentInventoryProvider,
    InventoryItem,
    InventoryProvider,
    InventoryStatus,
    InventoryTransaction,
    SqliteInventoryProvider,
    StockAction,
)

logger = logging.getLogger("inventory_service")


class InventoryService:
    """
    High-level inventory management service for the Owner Dashboard.
    Provides stock querying, adjustment, bulk operations, CSV/Excel import/export,
    and audit trail logging.
    """

    def __init__(
        self,
        provider: Optional[InventoryProvider] = None,
        tenant_id: str = "default",
    ):
        self._explicit_provider: Optional[InventoryProvider] = provider
        self.tenant_id = tenant_id
        self._supabase_provider: Optional[InventoryProvider] = None
        self._sqlite_provider: Optional[InventoryProvider] = None

    @property
    def is_supabase_primary(self) -> bool:
        """
        Returns True if Supabase is configured and serves as primary persistence.
        Automated test runs default to isolated SQLite unless USE_SUPABASE_IN_TESTS=1.
        """
        if self._explicit_provider is not None:
            return False
        import sys
        if any("unittest" in str(arg).lower() or "pytest" in str(arg).lower() for arg in sys.argv):
            if os.getenv("USE_SUPABASE_IN_TESTS") not in ("1", "true", "True"):
                return False
        try:
            from services.supabase_repository import SupabaseClient
            return SupabaseClient().is_configured
        except Exception:
            return False

    @property
    def provider(self) -> InventoryProvider:
        if self._explicit_provider is not None:
            return self._explicit_provider
        if self.is_supabase_primary:
            if self._supabase_provider is None:
                from services.inventory_provider import SupabaseInventoryProvider
                self._supabase_provider = SupabaseInventoryProvider(tenant_id=self.tenant_id)
            return self._supabase_provider
        if self._sqlite_provider is None:
            from services.inventory_provider import SqliteInventoryProvider
            self._sqlite_provider = SqliteInventoryProvider(tenant_id=self.tenant_id)
        return self._sqlite_provider

    def set_provider(self, provider: InventoryProvider) -> None:
        self._explicit_provider = provider

    def is_live_provider(self) -> bool:
        return self.provider.is_live()

    def check_availability(self, sku: str, requested_quantity: int) -> AvailabilityResult:
        return self.provider.check_availability(sku, requested_quantity)

    def get_available_quantity(self, sku: str) -> int:
        return self.provider.get_available_quantity(sku)

    def reserve_stock(self, sku: str, quantity: int) -> bool:
        return self.provider.reserve_stock(sku, quantity)

    def release_stock(self, sku: str, quantity: int) -> bool:
        return self.provider.release_stock(sku, quantity)

    def get_stock_status_for_quote(self, sku: str, requested_quantity: int) -> Optional[str]:
        if not self.is_live_provider():
            return None
        availability = self.check_availability(sku, requested_quantity)
        if availability.available:
            return f"📦 *Stock:* Available ({requested_quantity} units in stock)"
        elif availability.available_stock > 0:
            return f"📦 *Stock:* Limited Stock ({availability.available_stock} units available, {requested_quantity} requested)"
        elif availability.status == InventoryStatus.UNKNOWN:
            return "📦 *Stock:* Stock availability is subject to confirmation."
        else:
            return "📦 *Stock:* Currently Out of Stock"

    def get_inventory(self, sku: str) -> Optional[InventoryItem]:
        norm_sku = sku.strip().upper()
        item = self.provider.get_inventory(norm_sku)
        if item is None:
            return None
        prod = catalogue_service.sku_index.get(norm_sku)
        if prod:
            item.name = prod.get("name") or prod.get("subcategory") or prod.get("category") or "—"
            item.category = prod.get("category") or "—"
        return item

    def list_inventory(
        self,
        search: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        min_available: Optional[int] = None,
        max_available: Optional[int] = None,
        stock_attention_only: bool = False,
        limit: int = 1000,
        offset: int = 0,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        stock_attention: Optional[bool] = None,
        min_qty: Optional[int] = None,
        max_qty: Optional[int] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = "asc",
    ) -> Dict[str, Any]:
        """
        Lists inventory records for the catalogue with rich filtering, pagination, and sorting.
        Supports both (limit, offset) and (page, page_size) conventions seamlessly.
        """
        if stock_attention is not None:
            stock_attention_only = stock_attention
        if min_qty is not None:
            min_available = min_qty
        if max_qty is not None:
            max_available = max_qty
        if page is not None and page_size is not None:
            offset = (page - 1) * page_size
            limit = page_size

        all_prods = catalogue_service.products
        inv_map = self.provider.get_all_inventory_map()

        items: List[InventoryItem] = []
        for p in all_prods:
            raw_sku = p.get("sku", "").strip()
            norm_sku = raw_sku.upper()
            inv = inv_map.get(norm_sku)
            if inv is None:
                inv = InventoryItem(
                    sku=raw_sku,
                    name=p.get("name") or p.get("subcategory") or p.get("category") or "—",
                    category=p.get("category") or "—",
                    physical_stock=None,
                    reserved_stock=0,
                    reorder_level=100,
                    status=InventoryStatus.UNKNOWN,
                )
            else:
                inv.name = p.get("name") or p.get("subcategory") or p.get("category") or "—"
                inv.category = p.get("category") or "—"

            # Filter search (sku or name)
            if search:
                s = search.strip().lower()
                name_match = (inv.name or "").lower()
                sku_match = inv.sku.lower()
                if s not in name_match and s not in sku_match:
                    continue

            # Filter category
            if category and category.strip() != "":
                if (inv.category or "").strip().lower() != category.strip().lower():
                    continue

            # Filter status
            if status and status.strip() != "" and status.upper() != "ALL":
                if inv.status.value != status.strip().upper():
                    continue

            # Filter stock attention
            if stock_attention_only:
                if inv.status not in (InventoryStatus.LOW_STOCK, InventoryStatus.OUT_OF_STOCK, InventoryStatus.UNKNOWN):
                    continue

            # Filter min/max available
            avail = inv.raw_available
            if min_available is not None:
                if avail is None or avail < min_available:
                    continue
            if max_available is not None:
                if avail is None or avail > max_available:
                    continue

            items.append(inv)

        # Sorting
        rev = (sort_order or "asc").lower() == "desc"
        if sort_by in ("physical_stock", "physical_quantity"):
            items.sort(key=lambda x: (-1 if x.physical_stock is None else x.physical_stock), reverse=rev)
        elif sort_by in ("available_stock", "available_quantity"):
            items.sort(key=lambda x: (-1 if x.raw_available is None else x.raw_available), reverse=rev)
        elif sort_by == "name":
            items.sort(key=lambda x: (x.name or "").lower(), reverse=rev)
        elif sort_by == "category":
            items.sort(key=lambda x: (x.category or "").lower(), reverse=rev)
        else:
            items.sort(key=lambda x: x.sku, reverse=rev)

        total_count = len(items)
        paged_items = items[offset : offset + limit]

        return {
            "total_count": total_count,
            "total": total_count,
            "items": [item.to_dict() for item in paged_items],
            "limit": limit,
            "offset": offset,
            "page": page or (offset // limit + 1 if limit else 1),
            "page_size": page_size or limit,
        }

    def get_summary(self) -> Dict[str, Any]:
        """
        Returns inventory summary metrics across all 768 catalogue SKUs.
        Calculated in sub-millisecond memory pass.
        """
        all_prods = catalogue_service.products
        inv_map = self.provider.get_all_inventory_map()

        total_skus = len(all_prods)
        in_stock = 0
        low_stock = 0
        out_of_stock = 0
        unknown = 0
        coming_soon = 0
        supplier_confirm = 0

        for p in all_prods:
            norm_sku = p.get("sku", "").strip().upper()
            inv = inv_map.get(norm_sku)
            st = inv.status if inv else InventoryStatus.UNKNOWN
            if st == InventoryStatus.IN_STOCK:
                in_stock += 1
            elif st == InventoryStatus.LOW_STOCK:
                low_stock += 1
            elif st == InventoryStatus.OUT_OF_STOCK:
                out_of_stock += 1
            elif st == InventoryStatus.COMING_SOON:
                coming_soon += 1
            elif st == InventoryStatus.SUPPLIER_CONFIRMATION_REQUIRED:
                supplier_confirm += 1
            else:
                unknown += 1

        return {
            "total_skus": total_skus,
            "in_stock": in_stock,
            "low_stock": low_stock,
            "out_of_stock": out_of_stock,
            "unknown": unknown,
            "coming_soon": coming_soon,
            "supplier_confirmation_required": supplier_confirm,
            "stock_attention_count": low_stock + out_of_stock + unknown,
        }

    def has_transaction_reference(self, reference_type: str, reference_id: str) -> bool:
        if hasattr(self.provider, "has_transaction_reference"):
            return self.provider.has_transaction_reference(reference_type, reference_id)
        return False

    @staticmethod
    def _extract_field(row: Dict[str, Any], field_type: str) -> Any:
        """
        Extracts a field from a row dictionary using case-insensitive and punctuation-free aliases.
        Supports export headers:
        - 'SKU / Product Code' -> 'sku'
        - 'Physical Quantity' -> 'quantity'
        - 'Reorder Level' -> 'reorder_level'
        - 'Imported Cost' -> 'cost'
        """
        field_type = field_type.lower()
        target_alias_groups = {
            "sku": {"sku", "productcode", "code", "itemcode", "skuproductcode"},
            "name": {"productname", "name", "itemname", "description", "title"},
            "category": {"category", "cat", "subcategory"},
            "quantity": {"physicalquantity", "physicalstock", "quantity", "qty", "stock"},
            "reorder_level": {"reorderlevel", "reorder", "minstock", "threshold"},
            "cost": {"importedcost", "importedunitcost", "unitcost", "cost", "price", "unitprice"},
        }
        allowed_keys = target_alias_groups.get(field_type, {field_type})

        for k, v in row.items():
            if v is None:
                continue
            norm_k = re.sub(r"[^a-z0-9]", "", str(k).lower())
            if norm_k in allowed_keys:
                return v
        return None

    def adjust_stock(
        self,
        sku: str,
        action: StockAction,
        quantity: Optional[int] = None,
        reason: Optional[str] = None,
        notes: Optional[str] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        user: str = "owner",
        unit_cost: Optional[float] = None,
        reorder_level: Optional[int] = None,
    ) -> InventoryItem:
        if hasattr(self.provider, 'adjust_stock'):
            return self.provider.adjust_stock(
                sku=sku,
                action=action,
                quantity=quantity,
                reason=reason,
                notes=notes,
                reference_type=reference_type,
                reference_id=reference_id,
                user=user,
                unit_cost=unit_cost,
                reorder_level=reorder_level,
            )
        # Fallback for DevelopmentInventoryProvider
        norm_sku = sku.strip().upper()
        item = self.provider.get_inventory(norm_sku)
        if item is None:
            item = InventoryItem(sku=norm_sku, physical_stock=None, reserved_stock=0, reorder_level=100)
            self.provider._items[normalize_sku_key(norm_sku)] = item
        if reorder_level is not None:
            item.reorder_level = reorder_level
        if unit_cost is not None:
            item.unit_cost = unit_cost
        if quantity is None:
            if action in (StockAction.CORRECTION, StockAction.SET):
                item.physical_stock = None
                item.status = InventoryStatus.UNKNOWN
        else:
            qty_int = int(quantity)
            if action in (StockAction.ADD, StockAction.RECEIVE):
                item.physical_stock = (item.physical_stock or 0) + max(0, qty_int)
            elif action in (StockAction.REMOVE, StockAction.DAMAGED):
                item.physical_stock = max(0, (item.physical_stock or 0) - max(0, qty_int))
            elif action in (StockAction.CORRECTION, StockAction.SET):
                item.physical_stock = max(0, qty_int)

            avail = max(0, (item.physical_stock or 0) - (item.reserved_stock or 0))
            if avail == 0:
                item.status = InventoryStatus.OUT_OF_STOCK
            elif avail <= (item.reorder_level or 100):
                item.status = InventoryStatus.LOW_STOCK
            else:
                item.status = InventoryStatus.IN_STOCK
        item.last_updated = datetime.now().isoformat()
        return item

    def bulk_adjust(
        self,
        adjustments: List[Dict[str, Any]],
        user: str = "owner",
        reason: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        results = []
        for adj in adjustments:
            sku = adj.get("sku", "").strip().upper()
            action_str = adj.get("action", "ADD").upper()
            action = StockAction(action_str) if action_str in StockAction.__members__ else StockAction.ADD
            quantity = int(adj.get("quantity", 0))
            notes = adj.get("notes")
            unit_cost = adj.get("unit_cost")

            item = self.adjust_stock(
                sku=sku,
                action=action,
                quantity=quantity,
                reason=reason or "Bulk stock adjustment",
                notes=notes,
                reference_type="BULK_OPERATION",
                user=user,
                unit_cost=unit_cost,
            )
            results.append(item.to_dict())
        return results

    def bulk_update_status(
        self,
        skus: List[str],
        status: InventoryStatus,
        reason: Optional[str] = None,
        user: str = "owner",
    ) -> List[Dict[str, Any]]:
        results = []
        for sku in skus:
            if hasattr(self.provider, 'update_status'):
                item = self.provider.update_status(
                    sku=sku,
                    status=status,
                    reason=reason or f"Bulk status update to {status.value}",
                    user=user,
                )
            else:
                item = self.get_inventory(sku)
                if item:
                    item.status = status
                else:
                    item = InventoryItem(sku=sku, status=status)
            results.append(item.to_dict())
        return results

    def bulk_update_reorder_level(
        self,
        skus: List[str],
        reorder_level: int,
        reason: Optional[str] = None,
        user: str = "owner",
    ) -> List[Dict[str, Any]]:
        results = []
        for sku in skus:
            if hasattr(self.provider, 'update_reorder_level'):
                item = self.provider.update_reorder_level(
                    sku=sku,
                    reorder_level=reorder_level,
                    reason=reason or f"Bulk reorder level update to {reorder_level}",
                    user=user,
                )
            else:
                item = self.get_inventory(sku)
                if item:
                    item.reorder_level = reorder_level
                else:
                    item = InventoryItem(sku=sku, reorder_level=reorder_level)
            results.append(item.to_dict())
        return results

    def get_transactions(self, sku: Optional[str] = None, limit: int = 50) -> List[InventoryTransaction]:
        return self.provider.get_transactions(sku=sku, limit=limit)

    def get_history(self, sku: str, limit: int = 50) -> List[Dict[str, Any]]:
        txs = self.get_transactions(sku=sku, limit=limit)
        return [t.to_dict() for t in txs]

    def preview_import(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Validates an uploaded CSV/Excel row set without applying changes.
        Supports all export/template headers, normalized SKU matching via catalogue_service.get_by_sku(),
        reorder level parsing and validation, blank quantity -> UNKNOWN (not zero),
        duplicate detection, negative validation, and cost parsing.
        """
        seen_skus = set()
        duplicate_skus = set()
        validated_rows = []
        valid_count = 0
        invalid_count = 0
        inv_map = self.provider.get_all_inventory_map()

        for idx, r in enumerate(rows, start=1):
            raw_sku_val = self._extract_field(r, "sku")
            raw_sku = str(raw_sku_val).strip() if raw_sku_val is not None else ""
            raw_name = str(self._extract_field(r, "name") or "").strip()
            raw_qty = self._extract_field(r, "quantity")
            raw_reorder = self._extract_field(r, "reorder_level")
            raw_price = self._extract_field(r, "cost")

            # Check SKU existence in catalogue using get_by_sku() normalized matching
            prod = catalogue_service.get_by_sku(raw_sku) if raw_sku else None
            if not prod and raw_sku:
                prod = catalogue_service.sku_index.get(raw_sku.upper())
            canonical_sku = prod.get("sku") if prod else raw_sku

            BLANK_TOKENS = {"", "none", "-", "—", "–", "unknown", "n/a", "na", "null"}

            # Check quantity format (blank physical quantity is VALID, representing UNKNOWN)
            parsed_qty = None
            qty_error = None
            if raw_qty is None or str(raw_qty).strip().lower() in BLANK_TOKENS:
                parsed_qty = None
            else:
                clean_qty_str = str(raw_qty).replace(",", "").strip()
                try:
                    v = float(clean_qty_str)
                    if v < 0:
                        qty_error = "Quantity cannot be negative"
                    else:
                        parsed_qty = int(v)
                except ValueError:
                    qty_error = "Invalid numeric quantity"

            # Check reorder level format
            parsed_reorder = None
            reorder_error = None
            if raw_reorder is not None and str(raw_reorder).strip().lower() not in BLANK_TOKENS:
                clean_reorder_str = str(raw_reorder).replace(",", "").strip()
                try:
                    rv = float(clean_reorder_str)
                    if rv < 0:
                        reorder_error = "Reorder level cannot be negative"
                    else:
                        parsed_reorder = int(rv)
                except ValueError:
                    reorder_error = "Invalid numeric reorder level"

            # Check price/cost format
            parsed_cost = None
            cost_error = None
            if raw_price is not None and str(raw_price).strip().lower() not in BLANK_TOKENS:
                clean_cost_str = str(raw_price).replace("₹", "").replace("$", "").replace(",", "").strip()
                if clean_cost_str.lower() in BLANK_TOKENS:
                    parsed_cost = None
                else:
                    try:
                        c = float(clean_cost_str)
                        if c < 0:
                            cost_error = "Cost cannot be negative"
                        else:
                            parsed_cost = round(c, 2)
                    except ValueError:
                        cost_error = "Invalid numeric cost"

            # Check duplicates in file (keyed on canonical SKU)
            is_dup = False
            if canonical_sku:
                clean_dup_key = canonical_sku.upper().strip()
                if clean_dup_key in seen_skus:
                    duplicate_skus.add(clean_dup_key)
                    is_dup = True
                else:
                    seen_skus.add(clean_dup_key)

            # Determine row validity & status
            row_errors = []
            if not raw_sku:
                row_errors.append("Missing SKU / Product Code")
            elif not prod:
                row_errors.append(f"SKU '{raw_sku}' not found in catalogue")

            if qty_error:
                row_errors.append(qty_error)
            if reorder_error:
                row_errors.append(reorder_error)
            if cost_error:
                row_errors.append(cost_error)
            if is_dup:
                row_errors.append("Duplicate SKU in import file")

            row_status = "VALID" if not row_errors else "INVALID"
            if not row_errors:
                valid_count += 1
            else:
                invalid_count += 1

            # Current stock for preview
            current_inv = inv_map.get(canonical_sku.upper()) if canonical_sku else None
            curr_stock = current_inv.physical_stock if current_inv else None

            validated_rows.append({
                "row_number": idx,
                "sku": canonical_sku or raw_sku,
                "product_name": prod.get("name") if prod else raw_name,
                "category": prod.get("category") if prod else "—",
                "import_quantity": parsed_qty,
                "import_reorder_level": parsed_reorder,
                "current_physical_stock": curr_stock,
                "imported_unit_cost": parsed_cost,
                "status": row_status,
                "errors": row_errors,
            })

        return {
            "total_rows": len(rows),
            "valid_rows": valid_count,
            "invalid_rows": invalid_count,
            "duplicate_skus_count": len(duplicate_skus),
            "rows": validated_rows,
        }

    def apply_import(
        self,
        rows: List[Dict[str, Any]],
        mode: str = "add",  # "add" or "replace"
        user: str = "owner",
        reference_id: Optional[str] = None,
        confirm_replace: bool = False,
    ) -> Dict[str, Any]:
        """
        Applies validated rows from an import.
        mode='add': new_stock = current_stock + import_quantity (or unchanged if quantity is None)
        mode='replace': new_stock = import_quantity (or None/UNKNOWN if quantity is None)
        Requires explicit confirm_replace=True when mode='replace'.
        Checks reference_id idempotency to prevent duplicate adds on retry.
        """
        norm_mode = mode.lower()
        if norm_mode not in ("add", "replace"):
            raise ValueError(f"Invalid mode '{mode}'. Supported: 'add', 'replace'")

        if norm_mode == "replace" and not confirm_replace:
            raise ValueError("Confirmation required: 'confirm_replace' must be True to overwrite existing physical stock in replace mode.")

        # Idempotency check: if this reference_id was already applied, do not apply again!
        if reference_id and self.has_transaction_reference("IMPORT", reference_id):
            logger.warning(f"Import batch '{reference_id}' already applied. Skipping duplicate execution.")
            return {
                "mode": norm_mode,
                "reference_id": reference_id,
                "already_applied": True,
                "total_submitted": len(rows),
                "applied_count": 0,
                "skipped_count": len(rows),
                "applied_items": [],
                "skipped_items": [],
                "message": f"Batch '{reference_id}' was already applied previously. Idempotent skip."
            }

        preview = self.preview_import(rows)
        applied_items = []
        skipped_items = []

        action = StockAction.ADD if norm_mode == "add" else StockAction.SET

        for r in preview["rows"]:
            if r["status"] != "VALID":
                skipped_items.append(r)
                continue

            sku = r["sku"]
            qty = r["import_quantity"]
            reorder_level = r.get("import_reorder_level")
            cost = r["imported_unit_cost"]

            item = self.adjust_stock(
                sku=sku,
                action=action,
                quantity=qty,
                reason=f"Excel/CSV Import ({norm_mode.upper()} mode)",
                notes=f"Imported cost: ₹{cost}" if cost is not None else None,
                reference_type="IMPORT",
                reference_id=reference_id,
                user=user,
                unit_cost=cost,
                reorder_level=reorder_level,
            )
            applied_items.append(item.to_dict())

        return {
            "mode": norm_mode,
            "reference_id": reference_id,
            "already_applied": False,
            "total_submitted": len(rows),
            "applied_count": len(applied_items),
            "skipped_count": len(skipped_items),
            "applied_items": applied_items,
            "skipped_items": skipped_items,
        }

    def generate_template_csv(self) -> str:
        """
        Generates a blank inventory initialization template CSV with all 768 catalogue SKUs,
        product names, categories, reorder levels, and blank physical quantities.
        Headers match preview_import aliases so it can be uploaded directly.
        """
        all_prods = catalogue_service.products
        inv_map = self.provider.get_all_inventory_map()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Product Name",
            "SKU / Product Code",
            "Category",
            "Physical Quantity",
            "Reorder Level",
            "Imported Cost",
        ])

        for p in all_prods:
            sku = p.get("sku", "").strip().upper()
            name = p.get("name") or p.get("subcategory") or p.get("category") or "—"
            cat = p.get("category") or "—"
            inv = inv_map.get(sku)
            reorder = inv.reorder_level if inv else 100
            cost = f"{inv.unit_cost:.2f}" if (inv and inv.unit_cost is not None) else ""

            writer.writerow([name, sku, cat, "", reorder, cost])

        return output.getvalue()

    def export_csv(self) -> str:
        """Exports full catalogue inventory status as CSV."""
        all_prods = catalogue_service.products
        inv_map = self.provider.get_all_inventory_map()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Product Name",
            "SKU / Product Code",
            "Category",
            "Physical Quantity",
            "Reserved Quantity",
            "Available Quantity",
            "Reorder Level",
            "Imported Cost",
            "Status",
            "Last Updated",
        ])

        for p in all_prods:
            sku = p.get("sku", "").strip().upper()
            inv = inv_map.get(sku)
            name = p.get("name") or p.get("subcategory") or p.get("category") or "—"
            cat = p.get("category") or "—"
            phys = inv.physical_stock if (inv and inv.physical_stock is not None) else "UNKNOWN"
            res = inv.reserved_stock if inv else 0
            avail = inv.raw_available if (inv and inv.raw_available is not None) else "UNKNOWN"
            reorder = inv.reorder_level if inv else 100
            cost = f"₹{inv.unit_cost:.2f}" if (inv and inv.unit_cost is not None) else "—"
            status_str = inv.status.value if inv else "UNKNOWN"
            updated = inv.last_updated if inv else "—"

            writer.writerow([name, sku, cat, phys, res, avail, reorder, cost, status_str, updated])

        return output.getvalue()


# Global Singleton instance
inventory_service = InventoryService()
