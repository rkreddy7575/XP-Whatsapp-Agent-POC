import os
import csv
import io
import logging
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

    def adjust_stock(
        self,
        sku: str,
        action: StockAction,
        quantity: int,
        reason: Optional[str] = None,
        notes: Optional[str] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        user: str = "owner",
        unit_cost: Optional[float] = None,
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
            )
        # Fallback for DevelopmentInventoryProvider
        norm_sku = sku.strip().upper()
        item = self.provider.get_inventory(norm_sku)
        if item is None:
            item = InventoryItem(sku=norm_sku, physical_stock=0, reserved_stock=0)
            self.provider._items[normalize_sku_key(norm_sku)] = item
        if action in (StockAction.ADD, StockAction.RECEIVE):
            item.physical_stock = (item.physical_stock or 0) + max(0, quantity)
        elif action in (StockAction.REMOVE, StockAction.DAMAGED):
            item.physical_stock = max(0, (item.physical_stock or 0) - max(0, quantity))
        elif action in (StockAction.CORRECTION, StockAction.SET):
            item.physical_stock = max(0, quantity)
        item.last_updated = "2026-09-24T00:00:00"
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
        """
        seen_skus = set()
        duplicate_skus = set()
        validated_rows = []
        valid_count = 0
        invalid_count = 0
        inv_map = self.provider.get_all_inventory_map()

        for idx, r in enumerate(rows, start=1):
            raw_sku = str(r.get("sku") or r.get("product_code") or r.get("Product Code") or r.get("SKU") or "").strip().upper()
            raw_name = str(r.get("name") or r.get("product_name") or r.get("Product Name") or "").strip()
            raw_qty = r.get("quantity") or r.get("Quantity") or r.get("qty") or r.get("Stock")
            raw_price = r.get("price") or r.get("Price") or r.get("cost") or r.get("Unit Cost")

            # Check SKU existence in catalogue using in-memory index
            prod = catalogue_service.sku_index.get(raw_sku) if raw_sku else None
            canonical_sku = prod.get("sku") if prod else raw_sku

            # Check quantity format
            parsed_qty = None
            qty_error = None
            if raw_qty is not None and str(raw_qty).strip() != "":
                try:
                    v = float(str(raw_qty).replace(",", "").strip())
                    if v < 0:
                        qty_error = "Quantity cannot be negative"
                    else:
                        parsed_qty = int(v)
                except ValueError:
                    qty_error = "Invalid numeric quantity"
            else:
                qty_error = "Missing quantity"

            # Check price/cost format
            parsed_cost = None
            cost_error = None
            if raw_price is not None and str(raw_price).strip() != "":
                try:
                    c = float(str(raw_price).replace("₹", "").replace(",", "").strip())
                    if c < 0:
                        cost_error = "Cost cannot be negative"
                    else:
                        parsed_cost = round(c, 2)
                except ValueError:
                    cost_error = "Invalid numeric cost"

            # Check duplicates in file
            is_dup = False
            if canonical_sku:
                if canonical_sku in seen_skus:
                    duplicate_skus.add(canonical_sku)
                    is_dup = True
                else:
                    seen_skus.add(canonical_sku)

            # Determine row validity & status
            row_errors = []
            if not raw_sku:
                row_errors.append("Missing SKU / Product Code")
            elif not prod:
                row_errors.append(f"SKU '{raw_sku}' not found in catalogue")

            if qty_error:
                row_errors.append(qty_error)
            if cost_error:
                row_errors.append(cost_error)
            if is_dup:
                row_errors.append("Duplicate SKU in import file")

            row_status = "VALID" if not row_errors else "INVALID"
            if not row_errors:
                valid_count += 1
            else:
                invalid_count += 1

            # Get current stock
            current_inv = inv_map.get(canonical_sku) if canonical_sku else None
            curr_stock = current_inv.physical_stock if current_inv else None

            validated_rows.append({
                "row_number": idx,
                "sku": canonical_sku or raw_sku,
                "product_name": prod.get("name") if prod else raw_name,
                "category": prod.get("category") if prod else "—",
                "import_quantity": parsed_qty,
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
    ) -> Dict[str, Any]:
        """
        Applies validated rows from an import.
        mode='add': new_stock = current_stock + import_quantity
        mode='replace': new_stock = import_quantity
        """
        preview = self.preview_import(rows)
        applied_items = []
        skipped_items = []

        action = StockAction.ADD if mode.lower() == "add" else StockAction.SET

        for r in preview["rows"]:
            if r["status"] != "VALID":
                skipped_items.append(r)
                continue

            sku = r["sku"]
            qty = r["import_quantity"]
            cost = r["imported_unit_cost"]

            item = self.adjust_stock(
                sku=sku,
                action=action,
                quantity=qty,
                reason=f"Excel/CSV Import ({mode.upper()} mode)",
                notes=f"Imported cost: ₹{cost}" if cost is not None else None,
                reference_type="IMPORT",
                reference_id=reference_id or f"imp_{mode.lower()}",
                user=user,
                unit_cost=cost,
            )
            applied_items.append(item.to_dict())

        return {
            "mode": mode,
            "total_submitted": len(rows),
            "applied_count": len(applied_items),
            "skipped_count": len(skipped_items),
            "applied_items": applied_items,
            "skipped_items": skipped_items,
        }

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
