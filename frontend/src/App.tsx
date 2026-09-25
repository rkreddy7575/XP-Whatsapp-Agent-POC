import React, { useState, useEffect, useCallback, useMemo } from 'react';
import type {
  Order,
  OrderStatus,
  Enquiry,
  InventoryItem,
  InventorySummary,
  StockAction,
  InventoryStatus,
} from './types';
import {
  fetchOrders,
  updateOrderStatus,
  fetchEnquiries,
  getAuthToken,
  clearAuthToken,
  verifyAuth,
  fetchInventory,
  fetchInventorySummary,
  adjustStock,
  bulkAdjustStock,
  bulkUpdateStatus,
  bulkUpdateReorderLevel,
  downloadInventoryCsv,
} from './api';
import { Header, type DashboardTab } from './components/Header';
import { SummaryCards } from './components/SummaryCards';
import { OrderFilters } from './components/OrderFilters';
import { OrderList } from './components/OrderList';
import { OrderDetailsModal } from './components/OrderDetailsModal';
import { EnquiriesModal } from './components/EnquiriesModal';
import { LoginModal } from './components/LoginModal';
import { WhatsAppHealthCard } from './components/WhatsAppHealthCard';

// Inventory Components
import { InventorySummaryCards } from './components/InventorySummaryCards';
import { InventoryFilters } from './components/InventoryFilters';
import { InventoryList } from './components/InventoryList';
import { BulkActionsBar } from './components/BulkActionsBar';
import { StockAdjustmentModal } from './components/StockAdjustmentModal';
import { InventoryHistoryModal } from './components/InventoryHistoryModal';
import { BulkAdjustmentModal } from './components/BulkAdjustmentModal';
import { BulkStatusModal } from './components/BulkStatusModal';
import { BulkReorderModal } from './components/BulkReorderModal';
import { InventoryImportModal } from './components/InventoryImportModal';

export const App: React.FC = () => {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(() => !!getAuthToken());
  const [activeTab, setActiveTab] = useState<DashboardTab>('orders');

  // Orders State
  const [orders, setOrders] = useState<Order[]>([]);
  const [allOrders, setAllOrders] = useState<Order[]>([]);
  const [enquiries, setEnquiries] = useState<Enquiry[]>([]);
  const [ordersLoading, setOrdersLoading] = useState<boolean>(true);
  const [ordersError, setOrdersError] = useState<string | null>(null);
  const [orderSearch, setOrderSearch] = useState<string>('');
  const [orderStatusFilter, setOrderStatusFilter] = useState<string>('ALL');
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [isEnquiriesOpen, setIsEnquiriesOpen] = useState<boolean>(false);

  // Inventory State
  const [inventoryItems, setInventoryItems] = useState<InventoryItem[]>([]);
  const [inventorySummary, setInventorySummary] = useState<InventorySummary | null>(null);
  const [invLoading, setInvLoading] = useState<boolean>(false);
  const [invError, setInvError] = useState<string | null>(null);

  const [invSearch, setInvSearch] = useState<string>('');
  const [invCategory, setInvCategory] = useState<string>('');
  const [invStatus, setInvStatus] = useState<string>('ALL');
  const [invStockAttention, setInvStockAttention] = useState<boolean>(false);

  const [selectedSkus, setSelectedSkus] = useState<Set<string>>(new Set());

  // Modals
  const [adjustingItem, setAdjustingItem] = useState<InventoryItem | null>(null);
  const [historySku, setHistorySku] = useState<string | null>(null);
  const [isBulkAdjustOpen, setIsBulkAdjustOpen] = useState<boolean>(false);
  const [isBulkStatusOpen, setIsBulkStatusOpen] = useState<boolean>(false);
  const [isBulkReorderOpen, setIsBulkReorderOpen] = useState<boolean>(false);
  const [isImportModalOpen, setIsImportModalOpen] = useState<boolean>(false);

  const reqIdRef = React.useRef(0);

  // Auth verify
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

  // Load Orders
  const loadOrders = useCallback(async (isRefresh = false) => {
    if (!getAuthToken()) {
      setIsAuthenticated(false);
      setOrdersLoading(false);
      return;
    }

    const currentReqId = ++reqIdRef.current;
    setOrdersLoading(true);
    setOrdersError(null);
    try {
      const promises: [Promise<Order[]>, Promise<Enquiry[]>, Promise<Order[]>?] = [
        fetchOrders(orderStatusFilter, orderSearch),
        fetchEnquiries(),
      ];
      if (allOrders.length === 0 || isRefresh) {
        promises.push(fetchOrders('ALL', ''));
      }

      const [filtered, enqList, freshAll] = await Promise.all(promises);
      if (currentReqId !== reqIdRef.current) return;

      setOrders(filtered);
      setEnquiries(enqList);
      if (freshAll) {
        setAllOrders(freshAll);
      } else if (orderStatusFilter === 'ALL' && !orderSearch.trim()) {
        setAllOrders(filtered);
      }
    } catch (err: unknown) {
      if (currentReqId !== reqIdRef.current) return;
      if (err instanceof Error) {
        if (err.message.includes('401') || err.message.includes('Unauthorized')) {
          setIsAuthenticated(false);
          clearAuthToken();
        } else {
          setOrdersError(err.message);
        }
      } else {
        setOrdersError('Failed to connect to backend server.');
      }
    } finally {
      if (currentReqId === reqIdRef.current) {
        setOrdersLoading(false);
      }
    }
  }, [orderStatusFilter, orderSearch, allOrders.length]);

  // Load Inventory
  const loadInventory = useCallback(async () => {
    if (!getAuthToken()) return;
    setInvLoading(true);
    setInvError(null);
    try {
      const [listRes, sumRes] = await Promise.all([
        fetchInventory({
          search: invSearch,
          category: invCategory,
          status: invStatus,
          stock_attention_only: invStockAttention,
          limit: 1000,
        }),
        fetchInventorySummary(),
      ]);
      setInventoryItems(listRes.items);
      setInventorySummary(sumRes);
    } catch (err: any) {
      if (err.message?.includes('401') || err.message?.includes('Unauthorized')) {
        setIsAuthenticated(false);
        clearAuthToken();
      } else {
        setInvError(err.message || 'Failed to load inventory');
      }
    } finally {
      setInvLoading(false);
    }
  }, [invSearch, invCategory, invStatus, invStockAttention]);

  useEffect(() => {
    if (isAuthenticated) {
      if (activeTab === 'orders') {
        loadOrders();
      } else if (activeTab === 'inventory') {
        loadInventory();
      }
    }
  }, [isAuthenticated, activeTab, loadOrders, loadInventory]);

  // Extract unique categories from inventory
  const uniqueCategories = useMemo(() => {
    const cats = new Set<string>();
    inventoryItems.forEach((i) => {
      if (i.category && i.category !== '—') cats.add(i.category);
    });
    return Array.from(cats).sort();
  }, [inventoryItems]);

  const handleUpdateStatus = async (orderId: string, newStatus: OrderStatus) => {
    const updated = await updateOrderStatus(orderId, newStatus);
    setOrders((prev) => prev.map((o) => (o.order_id === orderId ? updated : o)));
    setAllOrders((prev) => prev.map((o) => (o.order_id === orderId ? updated : o)));
    setSelectedOrder(updated);
  };

  const handleLogout = () => {
    clearAuthToken();
    setIsAuthenticated(false);
    setOrders([]);
    setAllOrders([]);
    setEnquiries([]);
    setSelectedOrder(null);
    setInventoryItems([]);
    setSelectedSkus(new Set());
  };

  // Selection handlers
  const handleToggleSelectSku = (sku: string) => {
    setSelectedSkus((prev) => {
      const next = new Set(prev);
      if (next.has(sku)) next.delete(sku);
      else next.add(sku);
      return next;
    });
  };

  const handleToggleSelectAllSkus = () => {
    if (inventoryItems.every((i) => selectedSkus.has(i.sku))) {
      setSelectedSkus(new Set());
    } else {
      setSelectedSkus(new Set(inventoryItems.map((i) => i.sku)));
    }
  };

  // Stock Adjustment
  const handleSingleAdjustStock = async (
    sku: string,
    action: StockAction,
    quantity: number,
    unitCost?: number,
    reason?: string,
    notes?: string
  ) => {
    const updated = await adjustStock(sku, {
      action,
      quantity,
      unit_cost: unitCost,
      reason,
      notes,
    });
    setInventoryItems((prev) => prev.map((i) => (i.sku === sku ? updated : i)));
    // Refresh summary
    fetchInventorySummary().then(setInventorySummary);
  };

  // Bulk Adjustments
  const handleBulkAdjustStock = async (
    adjustments: Array<{ sku: string; action: StockAction; quantity: number; unit_cost?: number; notes?: string }>,
    reason?: string
  ) => {
    await bulkAdjustStock(adjustments, reason);
    setSelectedSkus(new Set());
    loadInventory();
  };

  const handleBulkStatus = async (skus: string[], status: InventoryStatus, reason?: string) => {
    await bulkUpdateStatus(skus, status, reason);
    setSelectedSkus(new Set());
    loadInventory();
  };

  const handleBulkReorder = async (skus: string[], reorderLevel: number, reason?: string) => {
    await bulkUpdateReorderLevel(skus, reorderLevel, reason);
    setSelectedSkus(new Set());
    loadInventory();
  };

  const pendingQuotesCount = enquiries.filter((e) => e.has_pending_quote).length;

  return (
    <div className="app-container" id="mudhra-dashboard-app">
      <Header
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onLogout={isAuthenticated ? handleLogout : undefined}
      />

      {/* ========================================================= */}
      {/* ORDERS VIEW                                              */}
      {/* ========================================================= */}
      {activeTab === 'orders' && (
        <div id="orders-tab-view">
          <SummaryCards
            orders={allOrders.length > 0 ? allOrders : orders}
            activeFilter={orderStatusFilter}
            onSelectFilter={(filterKey) => setOrderStatusFilter(filterKey)}
            pendingQuotesCount={pendingQuotesCount}
            onSelectEnquiries={() => setIsEnquiriesOpen(true)}
          />

          {isAuthenticated && <WhatsAppHealthCard />}

          {ordersError && (
            <div className="error-card" id="api-error-card">
              <div>
                <strong>Backend Connection Error:</strong>
                <p style={{ marginTop: '0.25rem', fontSize: '0.875rem' }}>{ordersError}</p>
              </div>
              <button className="retry-btn" onClick={() => loadOrders(false)}>
                Retry Connection
              </button>
            </div>
          )}

          <OrderFilters
            search={orderSearch}
            onSearchChange={setOrderSearch}
            statusFilter={orderStatusFilter}
            onStatusFilterChange={setOrderStatusFilter}
            onRefresh={() => loadOrders(true)}
            loading={ordersLoading}
            onOpenEnquiries={() => setIsEnquiriesOpen(true)}
            enquiriesCount={enquiries.length}
          />

          {ordersLoading ? (
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

          <OrderDetailsModal
            order={selectedOrder}
            onClose={() => setSelectedOrder(null)}
            onUpdateStatus={handleUpdateStatus}
          />

          <EnquiriesModal
            isOpen={isEnquiriesOpen}
            onClose={() => setIsEnquiriesOpen(false)}
            enquiries={enquiries}
          />
        </div>
      )}

      {/* ========================================================= */}
      {/* INVENTORY MANAGEMENT VIEW                                 */}
      {/* ========================================================= */}
      {activeTab === 'inventory' && (
        <div id="inventory-tab-view">
          <InventorySummaryCards
            summary={inventorySummary}
            activeStatus={invStatus}
            stockAttentionOnly={invStockAttention}
            onFilterStatus={(status) => {
              setInvStockAttention(false);
              setInvStatus(status);
            }}
            onToggleStockAttention={() => {
              setInvStockAttention((prev) => !prev);
            }}
          />

          {invError && (
            <div className="error-card" id="inv-error-card" style={{ marginBottom: '1.25rem' }}>
              <div>
                <strong>Inventory Error:</strong>
                <p style={{ marginTop: '0.25rem', fontSize: '0.875rem' }}>{invError}</p>
              </div>
              <button className="retry-btn" onClick={loadInventory}>
                Retry
              </button>
            </div>
          )}

          <InventoryFilters
            search={invSearch}
            category={invCategory}
            categories={uniqueCategories}
            status={invStatus}
            stockAttentionOnly={invStockAttention}
            onSearchChange={setInvSearch}
            onCategoryChange={setInvCategory}
            onStatusChange={setInvStatus}
            onToggleStockAttention={setInvStockAttention}
            onOpenImportModal={() => setIsImportModalOpen(true)}
            onExportCsv={downloadInventoryCsv}
            onRefresh={loadInventory}
            isLoading={invLoading}
          />

          <InventoryList
            items={inventoryItems}
            selectedSkus={selectedSkus}
            onToggleSelect={handleToggleSelectSku}
            onToggleSelectAll={handleToggleSelectAllSkus}
            onOpenAdjustModal={(item) => setAdjustingItem(item)}
            onOpenHistoryModal={(sku) => setHistorySku(sku)}
            isLoading={invLoading}
          />

          <BulkActionsBar
            selectedCount={selectedSkus.size}
            onBulkAdjust={() => setIsBulkAdjustOpen(true)}
            onBulkStatus={() => setIsBulkStatusOpen(true)}
            onBulkReorder={() => setIsBulkReorderOpen(true)}
            onClearSelection={() => setSelectedSkus(new Set())}
          />

          {/* Stock Adjustment Modal */}
          <StockAdjustmentModal
            item={adjustingItem}
            onClose={() => setAdjustingItem(null)}
            onSubmit={handleSingleAdjustStock}
          />

          {/* History Modal */}
          <InventoryHistoryModal
            sku={historySku}
            onClose={() => setHistorySku(null)}
          />

          {/* Bulk Modals */}
          {isBulkAdjustOpen && (
            <BulkAdjustmentModal
              skus={Array.from(selectedSkus)}
              onClose={() => setIsBulkAdjustOpen(false)}
              onSubmit={handleBulkAdjustStock}
            />
          )}

          {isBulkStatusOpen && (
            <BulkStatusModal
              skus={Array.from(selectedSkus)}
              onClose={() => setIsBulkStatusOpen(false)}
              onSubmit={handleBulkStatus}
            />
          )}

          {isBulkReorderOpen && (
            <BulkReorderModal
              skus={Array.from(selectedSkus)}
              onClose={() => setIsBulkReorderOpen(false)}
              onSubmit={handleBulkReorder}
            />
          )}

          {/* Import Modal */}
          {isImportModalOpen && (
            <InventoryImportModal
              onClose={() => setIsImportModalOpen(false)}
              onSuccess={loadInventory}
            />
          )}
        </div>
      )}

      {/* Authentication Login Dialog */}
      <LoginModal
        isOpen={!isAuthenticated}
        onLoginSuccess={() => {
          setIsAuthenticated(true);
          if (activeTab === 'orders') loadOrders(true);
          else loadInventory();
        }}
      />
    </div>
  );
};

export default App;
