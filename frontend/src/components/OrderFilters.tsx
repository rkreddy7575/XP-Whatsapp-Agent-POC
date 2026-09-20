import React from 'react';

interface OrderFiltersProps {
  search: string;
  onSearchChange: (value: string) => void;
  statusFilter: string;
  onStatusFilterChange: (value: string) => void;
  onRefresh: () => void;
  loading: boolean;
  onOpenEnquiries?: () => void;
  enquiriesCount?: number;
}

export const OrderFilters: React.FC<OrderFiltersProps> = ({
  search,
  onSearchChange,
  statusFilter,
  onStatusFilterChange,
  onRefresh,
  loading,
  onOpenEnquiries,
  enquiriesCount = 0,
}) => {
  return (
    <div className="controls-bar" id="order-filters-container">
      <div className="search-wrapper">
        <span className="search-icon">🔍</span>
        <input
          id="search-orders-input"
          type="text"
          className="search-input"
          placeholder="Search by Order ID, Customer, Phone..."
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
        />
      </div>

      <select
        id="status-filter-select"
        className="status-select"
        value={statusFilter}
        onChange={(e) => onStatusFilterChange(e.target.value)}
      >
        <option value="ALL">All Statuses</option>
        <option value="CONFIRMED">Confirmed</option>
        <option value="PROCESSING">Processing</option>
        <option value="READY_FOR_DISPATCH">Ready for Dispatch</option>
        <option value="DISPATCHED">Dispatched</option>
        <option value="DELIVERED">Delivered</option>
        <option value="CANCELLED">Cancelled</option>
        <option value="PENDING_CONFIRMATION">Pending Confirmation</option>
      </select>

      {onOpenEnquiries && (
        <button
          id="view-enquiries-btn"
          type="button"
          className="refresh-btn"
          onClick={onOpenEnquiries}
          title="View active customer WhatsApp chats & quotations"
          style={{ background: 'var(--bg-card)', borderColor: 'rgba(245, 158, 11, 0.35)', color: '#fbbf24' }}
        >
          <span>💬</span>
          <span>Leads & Enquiries {enquiriesCount > 0 ? `(${enquiriesCount})` : ''}</span>
        </button>
      )}

      <button
        id="refresh-orders-btn"
        className="refresh-btn"
        onClick={onRefresh}
        disabled={loading}
      >
        <span className={loading ? 'spinner' : ''} style={{ width: 14, height: 14, borderWidth: 2 }}>
          {!loading && '🔄'}
        </span>
        {loading ? 'Refreshing...' : 'Refresh'}
      </button>
    </div>
  );
};
