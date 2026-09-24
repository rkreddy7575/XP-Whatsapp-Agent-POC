import React, { useState, useEffect, useCallback } from 'react';
import type { Order, OrderStatus, Enquiry } from './types';
import { fetchOrders, updateOrderStatus, fetchEnquiries, getAuthToken, clearAuthToken, verifyAuth } from './api';
import { Header } from './components/Header';
import { SummaryCards } from './components/SummaryCards';
import { OrderFilters } from './components/OrderFilters';
import { OrderList } from './components/OrderList';
import { OrderDetailsModal } from './components/OrderDetailsModal';
import { EnquiriesModal } from './components/EnquiriesModal';
import { LoginModal } from './components/LoginModal';
import { WhatsAppHealthCard } from './components/WhatsAppHealthCard';

export const App: React.FC = () => {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(() => !!getAuthToken());
  const [orders, setOrders] = useState<Order[]>([]);
  const [allOrders, setAllOrders] = useState<Order[]>([]);
  const [enquiries, setEnquiries] = useState<Enquiry[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('ALL');

  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [isEnquiriesOpen, setIsEnquiriesOpen] = useState<boolean>(false);

  // Request counter to avoid race conditions when switching filters quickly
  const reqIdRef = React.useRef(0);

  // Initial check to verify token validity if already stored
  useEffect(() => {
    async function checkExistingToken() {
      if (getAuthToken()) {
        const isValid = await verifyAuth();
        if (!isValid) {
          clearAuthToken();
          setIsAuthenticated(false);
        }
      }
    }
    checkExistingToken();
  }, []);

  // Load orders and enquiries from API
  const loadOrders = useCallback(async (isRefresh = false) => {
    if (!getAuthToken()) {
      setIsAuthenticated(false);
      setLoading(false);
      return;
    }

    const currentReqId = ++reqIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const promises: [Promise<Order[]>, Promise<Enquiry[]>, Promise<Order[]>?] = [
        fetchOrders(statusFilter, search),
        fetchEnquiries(),
      ];
      if (allOrders.length === 0 || isRefresh) {
        promises.push(fetchOrders('ALL', ''));
      }

      const [filtered, enqList, freshAll] = await Promise.all(promises);

      // Discard if another request was initiated
      if (currentReqId !== reqIdRef.current) return;

      setOrders(filtered);
      setEnquiries(enqList);
      if (freshAll) {
        setAllOrders(freshAll);
      } else if (statusFilter === 'ALL' && !search.trim()) {
        setAllOrders(filtered);
      }
    } catch (err: unknown) {
      if (currentReqId !== reqIdRef.current) return;
      if (err instanceof Error) {
        if (err.message.includes('401') || err.message.includes('Unauthorized')) {
          setIsAuthenticated(false);
          clearAuthToken();
        } else {
          setError(err.message);
        }
      } else {
        setError('Failed to connect to backend server. Ensure FastAPI is running on port 8000.');
      }
    } finally {
      if (currentReqId === reqIdRef.current) {
        setLoading(false);
      }
    }
  }, [statusFilter, search, allOrders.length]);

  useEffect(() => {
    if (isAuthenticated) {
      loadOrders();
    }
  }, [isAuthenticated, loadOrders]);

  // Keep selected order state synchronized after updates
  useEffect(() => {
    if (selectedOrder) {
      const updated = orders.find((o) => o.order_id === selectedOrder.order_id);
      if (updated) {
        setSelectedOrder(updated);
      }
    }
  }, [orders]);

  const handleUpdateStatus = async (orderId: string, newStatus: OrderStatus) => {
    const updated = await updateOrderStatus(orderId, newStatus);
    // Update local state immediately
    setOrders((prev) =>
      prev.map((o) => (o.order_id === orderId ? updated : o))
    );
    setAllOrders((prev) =>
      prev.map((o) => (o.order_id === orderId ? updated : o))
    );
    setSelectedOrder(updated);
  };

  const handleLogout = () => {
    clearAuthToken();
    setIsAuthenticated(false);
    setOrders([]);
    setAllOrders([]);
    setEnquiries([]);
    setSelectedOrder(null);
  };

  const pendingQuotesCount = enquiries.filter((e) => e.has_pending_quote).length;

  return (
    <div className="app-container" id="mudhra-dashboard-app">
      <Header onLogout={isAuthenticated ? handleLogout : undefined} />

      {/* Summary Cards */}
      <SummaryCards
        orders={allOrders.length > 0 ? allOrders : orders}
        activeFilter={statusFilter}
        onSelectFilter={(filterKey) => setStatusFilter(filterKey)}
        pendingQuotesCount={pendingQuotesCount}
        onSelectEnquiries={() => setIsEnquiriesOpen(true)}
      />

      {/* WhatsApp Cloud API & Delivery Observability Health Card */}
      {isAuthenticated && <WhatsAppHealthCard />}

      {/* API Error State */}
      {error && (
        <div className="error-card" id="api-error-card">
          <div>
            <strong>Backend Connection Error:</strong>
            <p style={{ marginTop: '0.25rem', fontSize: '0.875rem' }}>{error}</p>
          </div>
          <button className="retry-btn" onClick={() => loadOrders(false)}>
            Retry Connection
          </button>
        </div>
      )}

      {/* Filter and Control Bar */}
      <OrderFilters
        search={search}
        onSearchChange={setSearch}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
        onRefresh={() => loadOrders(true)}
        loading={loading}
        onOpenEnquiries={() => setIsEnquiriesOpen(true)}
        enquiriesCount={enquiries.length}
      />

      {/* Main Order Content */}
      {loading ? (
        <div className="table-card" id="loading-orders-view">
          <div className="state-container">
            <div className="spinner" />
            <p style={{ marginTop: '1rem' }}>Loading WhatsApp orders...</p>
          </div>
        </div>
      ) : (
        <OrderList
          orders={orders}
          onSelectOrder={(order) => setSelectedOrder(order)}
        />
      )}

      {/* Order Details Drawer / Modal */}
      <OrderDetailsModal
        order={selectedOrder}
        onClose={() => setSelectedOrder(null)}
        onUpdateStatus={handleUpdateStatus}
      />

      {/* WhatsApp Leads & Active Enquiries Drawer */}
      <EnquiriesModal
        isOpen={isEnquiriesOpen}
        onClose={() => setIsEnquiriesOpen(false)}
        enquiries={enquiries}
      />

      {/* Authentication Login Dialog */}
      <LoginModal
        isOpen={!isAuthenticated}
        onLoginSuccess={() => {
          setIsAuthenticated(true);
          loadOrders(true);
        }}
      />
    </div>
  );
};

export default App;
