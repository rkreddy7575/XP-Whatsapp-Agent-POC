import type { Order, OrderStatus, Enquiry } from './types';

const API_BASE = import.meta.env.VITE_API_BASE ?? (import.meta.env.PROD ? '/api' : 'http://127.0.0.1:8000/api');
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
