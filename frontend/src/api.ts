import type {
  Order,
  OrderStatus,
  Enquiry,
  InventoryItem,
  InventorySummary,
  InventoryTransaction,
  ImportPreviewResult,
  ImportApplyResult,
  StockAction,
  InventoryStatus,
} from './types';

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api';
const TOKEN_KEY = 'mudhra_dashboard_token';

export function getAuthToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY);
}

export function setAuthToken(token: string, remember = false): void {
  sessionStorage.setItem(TOKEN_KEY, token);
  if (remember) {
    localStorage.setItem(TOKEN_KEY, token);
  }
}

export function clearAuthToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(TOKEN_KEY);
}

function getHeaders(): HeadersInit {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  const token = getAuthToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

export async function loginWithCredentials(credentials: {
  username?: string;
  password?: string;
  apiKey?: string;
  remember?: boolean;
}): Promise<string> {
  const body: Record<string, string> = {};
  if (credentials.apiKey) {
    body.api_key = credentials.apiKey;
  } else {
    body.username = credentials.username || 'admin';
    body.password = credentials.password || '';
  }

  const response = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    let errorMsg = 'Invalid username or password';
    try {
      const err = await response.json();
      errorMsg = err.detail || errorMsg;
    } catch {
      // Use fallback error
    }
    throw new Error(errorMsg);
  }

  const data = await response.json();
  setAuthToken(data.token, credentials.remember ?? false);
  return data.token;
}

export async function verifyAuth(): Promise<boolean> {
  const token = getAuthToken();
  if (!token) return false;

  try {
    const response = await fetch(`${API_BASE}/auth/verify`, {
      headers: getHeaders(),
    });
    return response.ok;
  } catch {
    return false;
  }
}

export async function fetchOrders(status?: string, search?: string): Promise<Order[]> {
  const params = new URLSearchParams();
  if (status && status !== 'ALL') {
    params.append('status', status);
  }
  if (search && search.trim()) {
    params.append('search', search.trim());
  }

  const query = params.toString() ? `?${params.toString()}` : '';
  const response = await fetch(`${API_BASE}/orders${query}`, {
    headers: getHeaders(),
  });

  if (!response.ok) {
    if (response.status === 401) {
      clearAuthToken();
      throw new Error('401 Unauthorized: Session expired or invalid credentials');
    }
    const errorText = await response.text();
    throw new Error(`Failed to load orders: ${response.status} ${errorText}`);
  }

  return response.json();
}

export async function fetchOrderById(orderId: string): Promise<Order> {
  const response = await fetch(`${API_BASE}/orders/${encodeURIComponent(orderId)}`, {
    headers: getHeaders(),
  });

  if (!response.ok) {
    if (response.status === 401) {
      clearAuthToken();
      throw new Error('401 Unauthorized: Session expired or invalid credentials');
    }
    const errorText = await response.text();
    throw new Error(`Failed to load order ${orderId}: ${response.status} ${errorText}`);
  }

  return response.json();
}

export async function updateOrderStatus(orderId: string, newStatus: OrderStatus): Promise<Order> {
  const response = await fetch(`${API_BASE}/orders/${encodeURIComponent(orderId)}/status`, {
    method: 'PATCH',
    headers: getHeaders(),
    body: JSON.stringify({ status: newStatus }),
  });

  if (!response.ok) {
    if (response.status === 401) {
      clearAuthToken();
      throw new Error('401 Unauthorized: Session expired or invalid credentials');
    }
    let errorDetail = '';
    try {
      const errJson = await response.json();
      errorDetail = errJson.detail || JSON.stringify(errJson);
    } catch {
      errorDetail = await response.text();
    }
    throw new Error(errorDetail || `Status update failed (${response.status})`);
  }

  return response.json();
}

export async function fetchEnquiries(): Promise<Enquiry[]> {
  const response = await fetch(`${API_BASE}/conversations/enquiries`, {
    headers: getHeaders(),
  });

  if (!response.ok) {
    if (response.status === 401) {
      clearAuthToken();
      throw new Error('401 Unauthorized: Session expired or invalid credentials');
    }
    return [];
  }

  return response.json();
}

export interface WhatsAppHealthDiagnostic {
  status: 'healthy' | 'degraded' | 'unhealthy';
  webhook: string;
  credentials: string;
  meta_api: string;
  phone_number_id?: string;
  token_status?: string;
  token_type?: string;
  meta_error?: {
    code?: number;
    error_subcode?: number;
    type?: string;
    message?: string;
  };
  metrics_24h?: {
    total_received: number;
    total_sent: number;
    total_delivered: number;
    total_failed: number;
    failure_rate_percent?: number | null;
  };
  last_incoming_message?: {
    phone: string;
    wamid?: string;
    timestamp: string;
  };
  last_outbound_message?: {
    phone: string;
    wamid?: string;
    status: string;
    timestamp: string;
  };
  recent_failures?: Array<{
    correlation_id: string;
    phone: string;
    timestamp: string;
    code?: number;
    type?: string;
    error_message?: string;
  }>;
}

export async function fetchWhatsAppHealth(): Promise<WhatsAppHealthDiagnostic> {
  const response = await fetch(`${API_BASE}/dashboard/whatsapp-health`, {
    headers: getHeaders(),
  });

  if (!response.ok) {
    if (response.status === 401) {
      clearAuthToken();
      throw new Error('401 Unauthorized: Session expired or invalid credentials');
    }
    throw new Error(`Failed to load WhatsApp health: ${response.status}`);
  }

  return response.json();
}

// ==========================================
// INVENTORY MANAGEMENT API (Phase 1)
// ==========================================

export interface InventoryQueryParams {
  search?: string;
  category?: string;
  status?: string;
  min_available?: number;
  max_available?: number;
  stock_attention_only?: boolean;
  limit?: number;
  offset?: number;
}

export interface InventoryListResponse {
  total_count: number;
  items: InventoryItem[];
  limit: number;
  offset: number;
}

export async function fetchInventory(params?: InventoryQueryParams): Promise<InventoryListResponse> {
  const query = new URLSearchParams();
  if (params?.search && params.search.trim()) query.append('search', params.search.trim());
  if (params?.category && params.category.trim()) query.append('category', params.category.trim());
  if (params?.status && params.status !== 'ALL') query.append('status', params.status);
  if (params?.min_available !== undefined) query.append('min_available', String(params.min_available));
  if (params?.max_available !== undefined) query.append('max_available', String(params.max_available));
  if (params?.stock_attention_only) query.append('stock_attention_only', 'true');
  if (params?.limit !== undefined) query.append('limit', String(params.limit));
  if (params?.offset !== undefined) query.append('offset', String(params.offset));

  const qs = query.toString() ? `?${query.toString()}` : '';
  const response = await fetch(`${API_BASE}/inventory${qs}`, {
    headers: getHeaders(),
  });

  if (!response.ok) {
    if (response.status === 401) {
      clearAuthToken();
      throw new Error('401 Unauthorized');
    }
    throw new Error(`Failed to load inventory: ${response.status}`);
  }

  return response.json();
}

export async function fetchInventorySummary(): Promise<InventorySummary> {
  const response = await fetch(`${API_BASE}/inventory/summary`, {
    headers: getHeaders(),
  });

  if (!response.ok) {
    if (response.status === 401) {
      clearAuthToken();
      throw new Error('401 Unauthorized');
    }
    throw new Error(`Failed to load inventory summary: ${response.status}`);
  }

  return response.json();
}

export async function fetchInventoryItem(sku: string): Promise<InventoryItem> {
  const response = await fetch(`${API_BASE}/inventory/${encodeURIComponent(sku)}`, {
    headers: getHeaders(),
  });

  if (!response.ok) {
    if (response.status === 401) {
      clearAuthToken();
      throw new Error('401 Unauthorized');
    }
    throw new Error(`Failed to load SKU ${sku}: ${response.status}`);
  }

  return response.json();
}

export async function fetchInventoryHistory(sku: string, limit = 50): Promise<InventoryTransaction[]> {
  const response = await fetch(`${API_BASE}/inventory/${encodeURIComponent(sku)}/history?limit=${limit}`, {
    headers: getHeaders(),
  });

  if (!response.ok) {
    if (response.status === 401) {
      clearAuthToken();
      throw new Error('401 Unauthorized');
    }
    throw new Error(`Failed to load history for SKU ${sku}: ${response.status}`);
  }

  return response.json();
}

export interface StockAdjustPayload {
  action: StockAction;
  quantity: number;
  reason?: string;
  notes?: string;
  unit_cost?: number;
}

export async function adjustStock(sku: string, payload: StockAdjustPayload): Promise<InventoryItem> {
  const response = await fetch(`${API_BASE}/inventory/${encodeURIComponent(sku)}/adjust`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    if (response.status === 401) {
      clearAuthToken();
      throw new Error('401 Unauthorized');
    }
    let errorDetail = 'Stock adjustment failed';
    try {
      const err = await response.json();
      errorDetail = err.detail || errorDetail;
    } catch {
      // fallback
    }
    throw new Error(errorDetail);
  }

  return response.json();
}

export async function bulkAdjustStock(adjustments: Array<{ sku: string; action: StockAction; quantity: number; unit_cost?: number; notes?: string }>, reason?: string): Promise<InventoryItem[]> {
  const response = await fetch(`${API_BASE}/inventory/bulk/adjust`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ adjustments, reason }),
  });

  if (!response.ok) {
    throw new Error(`Bulk stock adjustment failed: ${response.status}`);
  }

  return response.json();
}

export async function bulkUpdateStatus(skus: string[], status: InventoryStatus, reason?: string): Promise<InventoryItem[]> {
  const response = await fetch(`${API_BASE}/inventory/bulk/status`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ skus, status, reason }),
  });

  if (!response.ok) {
    throw new Error(`Bulk status update failed: ${response.status}`);
  }

  return response.json();
}

export async function bulkUpdateReorderLevel(skus: string[], reorder_level: number, reason?: string): Promise<InventoryItem[]> {
  const response = await fetch(`${API_BASE}/inventory/bulk/reorder-level`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ skus, reorder_level, reason }),
  });

  if (!response.ok) {
    throw new Error(`Bulk reorder level update failed: ${response.status}`);
  }

  return response.json();
}

export async function previewInventoryImport(rows: Array<Record<string, any>>): Promise<ImportPreviewResult> {
  const response = await fetch(`${API_BASE}/inventory/import/preview`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(rows),
  });

  if (!response.ok) {
    let errorMsg = 'Import preview failed';
    try {
      const err = await response.json();
      errorMsg = err.detail || errorMsg;
    } catch {
      // fallback
    }
    throw new Error(errorMsg);
  }

  return response.json();
}

export async function applyInventoryImport(rows: Array<Record<string, any>>, mode: 'add' | 'replace'): Promise<ImportApplyResult> {
  const response = await fetch(`${API_BASE}/inventory/import/apply`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ rows, mode }),
  });

  if (!response.ok) {
    let errorMsg = 'Import apply failed';
    try {
      const err = await response.json();
      errorMsg = err.detail || errorMsg;
    } catch {
      // fallback
    }
    throw new Error(errorMsg);
  }

  return response.json();
}

export async function downloadInventoryCsv(): Promise<void> {
  const response = await fetch(`${API_BASE}/inventory/export`, {
    headers: getHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Failed to export CSV: ${response.status}`);
  }

  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `mudhra_inventory_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
}
