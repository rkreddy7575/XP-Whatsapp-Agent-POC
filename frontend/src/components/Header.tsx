import React from 'react';

export type DashboardTab = 'orders' | 'inventory';

interface HeaderProps {
  activeTab: DashboardTab;
  onTabChange: (tab: DashboardTab) => void;
  onLogout?: () => void;
}

export const Header: React.FC<HeaderProps> = ({ activeTab, onTabChange, onLogout }) => {
  return (
    <header className="dashboard-header" id="dashboard-header">
      <div className="brand-section">
        <h1>
          <span>💼</span> MUDHRA OWNER DASHBOARD
        </h1>
        <p>WhatsApp B2B Corporate Gifting Sales & Inventory Management</p>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '1.25rem', flexWrap: 'wrap' }}>
        {/* Navigation Tabs */}
        <div
          style={{
            display: 'flex',
            background: 'rgba(15, 23, 42, 0.7)',
            padding: '0.25rem',
            borderRadius: '8px',
            border: '1px solid rgba(255, 255, 255, 0.1)',
          }}
          id="dashboard-nav-tabs"
        >
          <button
            type="button"
            onClick={() => onTabChange('orders')}
            id="nav-tab-orders"
            style={{
              background: activeTab === 'orders' ? '#6366f1' : 'transparent',
              color: activeTab === 'orders' ? '#ffffff' : '#94a3b8',
              border: 'none',
              borderRadius: '6px',
              padding: '0.45rem 0.9rem',
              fontSize: '0.85rem',
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '0.35rem',
              transition: 'all 0.15s ease',
            }}
          >
            <span>📋</span>
            <span>Orders & Quotes</span>
          </button>

          <button
            type="button"
            onClick={() => onTabChange('inventory')}
            id="nav-tab-inventory"
            style={{
              background: activeTab === 'inventory' ? '#6366f1' : 'transparent',
              color: activeTab === 'inventory' ? '#ffffff' : '#94a3b8',
              border: 'none',
              borderRadius: '6px',
              padding: '0.45rem 0.9rem',
              fontSize: '0.85rem',
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '0.35rem',
              transition: 'all 0.15s ease',
            }}
          >
            <span>📦</span>
            <span>Inventory Management</span>
          </button>
        </div>

        <div className="header-status-badge">
          <span className="pulse-dot" />
          <span>768 SKUs Persisted</span>
        </div>

        {onLogout && (
          <button
            type="button"
            onClick={onLogout}
            id="header-logout-btn"
            style={{
              background: 'rgba(244, 63, 94, 0.15)',
              border: '1px solid rgba(244, 63, 94, 0.3)',
              color: '#fb7185',
              padding: '0.4rem 0.85rem',
              borderRadius: '6px',
              fontSize: '0.8rem',
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '0.35rem',
              transition: 'all 0.2s',
            }}
          >
            <span>🔒</span>
            <span>Sign Out</span>
          </button>
        )}
      </div>
    </header>
  );
};
