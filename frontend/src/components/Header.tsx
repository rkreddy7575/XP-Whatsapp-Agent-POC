interface HeaderProps {
  onLogout?: () => void;
}

export const Header: React.FC<HeaderProps> = ({ onLogout }) => {
  return (
    <header className="dashboard-header" id="dashboard-header">
      <div className="brand-section">
        <h1>
          <span>💼</span> MUDHRA ORDER DASHBOARD
        </h1>
        <p>WhatsApp B2B Corporate Gifting Sales & Order Management</p>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
        <div className="header-status-badge">
          <span className="pulse-dot" />
          <span>SQLite Live • Automated Snapshots</span>
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
