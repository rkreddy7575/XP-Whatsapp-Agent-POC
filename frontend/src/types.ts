export type OrderStatus =
  | 'PENDING_CONFIRMATION'
  | 'CONFIRMED'
  | 'PROCESSING'
  | 'READY_FOR_DISPATCH'
  | 'DISPATCHED'
  | 'DELIVERED'
  | 'CANCELLED';

export interface OrderItem {
  sku: string;
  quantity: number;
  unit_price: number;
  gst_rate: number;
  gst_amount: number;
  line_total: number;
}

export interface Order {
  order_id: string;
  customer_phone: string;
  customer_name?: string | null;
  status: OrderStatus;
  subtotal: number;
  gst_amount: number;
  grand_total: number;
  currency: string;
  inventory_status: string;
  created_at: string;
  items: OrderItem[];
}

export interface Enquiry {
  conversation_id: string;
  customer_name?: string | null;
  customer_phone: string;
  enquiry_text?: string | null;
  last_intent: string | null;
  products_discussed?: string[];
  selected_sku: string | null;
  selected_quantity: number | null;
  candidate_count: number;
  has_pending_quote: boolean;
  quotation_status: 'ACTIVE_QUOTE' | 'ORDERED' | 'PRODUCT_SELECTED' | 'INQUIRING';
  pending_quote?: {
    sku?: string;
    quantity?: number;
    unit_price_excl_gst?: number;
    total_price_incl_gst?: number;
  } | null;
  latest_order_id?: string | null;
  latest_order_status?: string | null;
  created_at: string;
  updated_at: string;
}

export type InventoryStatus =
  | 'IN_STOCK'
  | 'LOW_STOCK'
  | 'OUT_OF_STOCK'
  | 'COMING_SOON'
  | 'SUPPLIER_CONFIRMATION_REQUIRED'
  | 'UNKNOWN';

export type StockAction = 'RECEIVE' | 'ADD' | 'REMOVE' | 'CORRECTION' | 'DAMAGED' | 'SET';

export interface InventoryItem {
  sku: string;
  name?: string | null;
  category?: string | null;
  physical_stock: number | null;
  reserved_stock: number;
  available_stock: number | null;
  reorder_level: number;
  unit_cost: number | null; // Labeled as "Imported Cost/Price"
  status: InventoryStatus;
  last_updated: string | null;
  supplier_id?: string | null;
  source?: string;
}

export interface InventoryTransaction {
  id: string;
  sku: string;
  transaction_type: string;
  quantity_change: number;
  quantity_before: number | null;
  quantity_after: number | null;
  reason?: string | null;
  reference_type?: string | null;
  reference_id?: string | null;
  notes?: string | null;
  created_by: string;
  created_at: string;
}

export interface InventorySummary {
  total_skus: number;
  in_stock: number;
  low_stock: number;
  out_of_stock: number;
  unknown: number;
  coming_soon: number;
  supplier_confirmation_required: number;
  stock_attention_count: number;
}

export interface ImportPreviewRow {
  row_number: number;
  sku: string;
  product_name?: string | null;
  category?: string | null;
  import_quantity: number | null;
  current_physical_stock: number | null;
  imported_unit_cost: number | null;
  status: 'VALID' | 'INVALID';
  errors: string[];
}

export interface ImportPreviewResult {
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_skus_count: number;
  rows: ImportPreviewRow[];
}

export interface ImportApplyResult {
  mode: 'add' | 'replace';
  total_submitted: number;
  applied_count: number;
  skipped_count: number;
  applied_items: InventoryItem[];
  skipped_items: ImportPreviewRow[];
}
