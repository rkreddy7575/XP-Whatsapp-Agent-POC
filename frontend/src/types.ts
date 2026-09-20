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

