import React from 'react';

interface Props {
  search: string;
  category: string;
  categories: string[];
  status: string;
  stockAttentionOnly: boolean;
  onSearchChange: (val: string) => void;
  onCategoryChange: (val: string) => void;
  onStatusChange: (val: string) => void;
  onToggleStockAttention: (val: boolean) => void;
  onOpenImportModal: () => void;
  onExportCsv: () => void;
  onRefresh: () => void;
  isLoading: boolean;
}

export const InventoryFilters: React.FC<Props> = ({
  search,
  category,
  categories,
  status,
  stockAttentionOnly,
  onSearchChange,
  onCategoryChange,
  onStatusChange,
  onToggleStockAttention,
  onOpenImportModal,
  onExportCsv,
  onRefresh,
  isLoading,
}) => {
  return (
    <div
      style={{
        background: 'rgba(23, 32, 54, 0.75)',
        border: '1px solid rgba(255, 255, 255, 0.08)',
        borderRadius: '10px',
        padding: '1rem 1.25rem',
        marginBottom: '1.25rem',
        display: 'flex',
        flexWrap: 'wrap',
        gap: '1rem',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}
      id="inventory-filters-bar"
    >
      {/* Left controls */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', alignItems: 'center', flex: 1 }}>
        {/* Search */}
        <div style={{ position: 'relative', minWidth: '220px', flex: 1 }}>
          <input
            type="text"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search by SKU / Product Name..."
            id="inv-search-input"
            style={{
              width: '100%',
              background: 'rgba(15, 23, 42, 0.8)',
              border: '1px solid rgba(255, 255, 255, 0.12)',
              borderRadius: '6px',
              color: '#f8fafc',
              padding: '0.55rem 0.85rem',
              fontSize: '0.85rem',
              outline: 'none',
            }}
          />
        </div>

        {/* Category dropdown */}
        <select
          value={category}
          onChange={(e) => onCategoryChange(e.target.value)}
          id="inv-category-select"
          style={{
            background: 'rgba(15, 23, 42, 0.8)',
            border: '1px solid rgba(255, 255, 255, 0.12)',
            borderRadius: '6px',
            color: '#f8fafc',
            padding: '0.55rem 0.85rem',
            fontSize: '0.85rem',
            outline: 'none',
          }}
        >
          <option value="">All Categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>

        {/* Status dropdown */}
        <select
          value={status}
          onChange={(e) => onStatusChange(e.target.value)}
          id="inv-status-select"
          style={{
            background: 'rgba(15, 23, 42, 0.8)',
            border: '1px solid rgba(255, 255, 255, 0.12)',
            borderRadius: '6px',
            color: '#f8fafc',
            padding: '0.55rem 0.85rem',
            fontSize: '0.85rem',
            outline: 'none',
          }}
        >
          <option value="ALL">All Statuses</option>
          <option value="IN_STOCK">In Stock</option>
          <option value="LOW_STOCK">Low Stock</option>
          <option value="OUT_OF_STOCK">Out of Stock</option>
          <option value="UNKNOWN">Unknown</option>
          <option value="COMING_SOON">Coming Soon</option>
          <option value="SUPPLIER_CONFIRMATION_REQUIRED">Supplier Confirmation</option>
        </select>

        {/* Stock Attention Checkbox */}
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.4rem',
            fontSize: '0.85rem',
            color: stockAttentionOnly ? '#f43f5e' : '#94a3b8',
            cursor: 'pointer',
            userSelect: 'none',
          }}
        >
          <input
            type="checkbox"
            checked={stockAttentionOnly}
            onChange={(e) => onToggleStockAttention(e.target.checked)}
            id="inv-stock-attention-checkbox"
          />
          <span>Stock Attention</span>
        </label>
      </div>

      {/* Right action buttons */}
      <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'center' }}>
        <button
          type="button"
          onClick={onRefresh}
          disabled={isLoading}
          id="inv-refresh-btn"
          style={{
            background: 'rgba(255, 255, 255, 0.06)',
            border: '1px solid rgba(255, 255, 255, 0.12)',
            color: '#f8fafc',
            padding: '0.5rem 0.85rem',
            borderRadius: '6px',
            fontSize: '0.85rem',
            fontWeight: 500,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '0.35rem',
          }}
        >
          <span>🔄</span>
          <span>{isLoading ? 'Loading...' : 'Refresh'}</span>
        </button>

        <button
          type="button"
          onClick={onExportCsv}
          id="inv-export-btn"
          style={{
            background: 'rgba(99, 102, 241, 0.15)',
            border: '1px solid rgba(99, 102, 241, 0.3)',
            color: '#818cf8',
            padding: '0.5rem 0.85rem',
            borderRadius: '6px',
            fontSize: '0.85rem',
            fontWeight: 600,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '0.35rem',
          }}
        >
          <span>📥</span>
          <span>Export CSV</span>
        </button>

        <button
          type="button"
          onClick={onOpenImportModal}
          id="inv-import-btn"
          style={{
            background: '#6366f1',
            border: 'none',
            color: '#ffffff',
            padding: '0.5rem 1rem',
            borderRadius: '6px',
            fontSize: '0.85rem',
            fontWeight: 600,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '0.35rem',
          }}
        >
          <span>📤</span>
          <span>Import CSV/Excel</span>
        </button>
      </div>
    </div>
  );
};
