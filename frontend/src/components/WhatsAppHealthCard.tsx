import React, { useState, useEffect } from 'react';
import { fetchWhatsAppHealth, type WhatsAppHealthDiagnostic } from '../api';

export const WhatsAppHealthCard: React.FC = () => {
  const [health, setHealth] = useState<WhatsAppHealthDiagnostic | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<boolean>(false);

  const loadHealth = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchWhatsAppHealth();
      setHealth(data);
    } catch (err: unknown) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Failed to fetch WhatsApp health diagnostics.');
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadHealth();
  }, []);

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'healthy':
      case 'valid':
      case 'configured':
      case 'reachable':
        return <span style={{ color: '#34d399', fontWeight: 600 }}>● {status.toUpperCase()}</span>;
      case 'degraded':
      case 'expired':
        return <span style={{ color: '#fbbf24', fontWeight: 600 }}>▲ {status.toUpperCase()}</span>;
      case 'unhealthy':
      case 'invalid':
      case 'unreachable':
      case 'unconfigured':
      default:
        return <span style={{ color: '#fb7185', fontWeight: 600 }}>✖ {status.toUpperCase()}</span>;
    }
  };

  return (
    <div
      style={{
        backgroundColor: 'var(--bg-card)',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border-color)',
        padding: '1.25rem',
        marginBottom: '1.5rem',
        backdropFilter: 'blur(8px)',
      }}
      id="whatsapp-health-section"
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          cursor: 'pointer',
        }}
        onClick={() => setCollapsed(!collapsed)}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <span style={{ fontSize: '1.25rem' }}>📱</span>
          <div>
            <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 600, color: 'var(--text-primary)' }}>
              WhatsApp Engine & Webhook Reliability
            </h3>
            <p style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
              Cloud API Authentication, Delivery Lifecycle & Observability
            </p>
          </div>
          {health && (
            <div style={{ marginLeft: '1rem', fontSize: '0.85rem' }}>
              {getStatusBadge(health.status)}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button
            onClick={(e) => {
              e.stopPropagation();
              loadHealth();
            }}
            disabled={loading}
            style={{
              background: 'transparent',
              border: '1px solid var(--border-color)',
              color: 'var(--text-secondary)',
              borderRadius: 'var(--radius-sm)',
              padding: '0.25rem 0.6rem',
              fontSize: '0.75rem',
              cursor: 'pointer',
            }}
          >
            {loading ? 'Probing...' : 'Refresh'}
          </button>
          <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
            {collapsed ? '▼' : '▲'}
          </span>
        </div>
      </div>

      {!collapsed && (
        <div style={{ marginTop: '1rem', borderTop: '1px solid var(--border-color)', paddingTop: '1rem' }}>
          {error && (
            <div
              style={{
                backgroundColor: 'rgba(244, 63, 94, 0.1)',
                border: '1px solid rgba(244, 63, 94, 0.3)',
                padding: '0.75rem',
                borderRadius: 'var(--radius-sm)',
                color: '#fb7185',
                fontSize: '0.85rem',
                marginBottom: '1rem',
              }}
            >
              ⚠ Error probing WhatsApp health: {error}
            </div>
          )}

          {health && (
            <>
              {health.meta_error && (
                <div
                  style={{
                    backgroundColor: 'rgba(245, 158, 11, 0.1)',
                    border: '1px solid rgba(245, 158, 11, 0.3)',
                    padding: '0.75rem',
                    borderRadius: 'var(--radius-sm)',
                    color: '#fbbf24',
                    fontSize: '0.85rem',
                    marginBottom: '1rem',
                  }}
                >
                  <strong>Auth Diagnostic: </strong>
                  Meta {health.meta_error.type || 'Error'} (Code {health.meta_error.code}, Subcode {health.meta_error.error_subcode}):{' '}
                  {health.meta_error.message}
                  {health.meta_error.code === 190 && (
                    <div style={{ marginTop: '0.25rem', color: 'var(--text-secondary)', fontSize: '0.75rem' }}>
                      Tip: Temporary user access tokens expire after 24 hours. For permanent production uptime, configure a Meta System User permanent token.
                    </div>
                  )}
                </div>
              )}

              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                  gap: '1rem',
                  marginBottom: '1rem',
                }}
              >
                <div style={{ backgroundColor: 'rgba(0,0,0,0.2)', padding: '0.75rem', borderRadius: 'var(--radius-sm)' }}>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>WEBHOOK PIPELINE</div>
                  <div style={{ fontSize: '0.9rem', marginTop: '0.25rem' }}>{getStatusBadge(health.webhook)}</div>
                </div>

                <div style={{ backgroundColor: 'rgba(0,0,0,0.2)', padding: '0.75rem', borderRadius: 'var(--radius-sm)' }}>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>META AUTH / TOKEN</div>
                  <div style={{ fontSize: '0.9rem', marginTop: '0.25rem' }}>{getStatusBadge(health.credentials)}</div>
                  {health.token_type && (
                    <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)' }}>Type: {health.token_type}</div>
                  )}
                </div>

                <div style={{ backgroundColor: 'rgba(0,0,0,0.2)', padding: '0.75rem', borderRadius: 'var(--radius-sm)' }}>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>LAST INCOMING MSG</div>
                  <div style={{ fontSize: '0.85rem', marginTop: '0.25rem', color: 'var(--text-primary)' }}>
                    {health.last_incoming_message ? (
                      <>
                        +{health.last_incoming_message.phone}
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                          {new Date(health.last_incoming_message.timestamp).toLocaleTimeString()}
                        </div>
                      </>
                    ) : (
                      'None recorded'
                    )}
                  </div>
                </div>

                <div style={{ backgroundColor: 'rgba(0,0,0,0.2)', padding: '0.75rem', borderRadius: 'var(--radius-sm)' }}>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>LAST OUTBOUND STATUS</div>
                  <div style={{ fontSize: '0.85rem', marginTop: '0.25rem', color: 'var(--text-primary)' }}>
                    {health.last_outbound_message ? (
                      <>
                        <span style={{ fontWeight: 600 }}>{health.last_outbound_message.status}</span> (+{health.last_outbound_message.phone})
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                          {new Date(health.last_outbound_message.timestamp).toLocaleTimeString()}
                        </div>
                      </>
                    ) : (
                      'None recorded'
                    )}
                  </div>
                </div>
              </div>

              {health.metrics_24h && (
                <div
                  style={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: '1.5rem',
                    fontSize: '0.8rem',
                    color: 'var(--text-secondary)',
                    backgroundColor: 'rgba(0,0,0,0.15)',
                    padding: '0.6rem 0.9rem',
                    borderRadius: 'var(--radius-sm)',
                  }}
                >
                  <span>24h Inbound: <strong style={{ color: 'var(--text-primary)' }}>{health.metrics_24h.total_received}</strong></span>
                  <span>24h Sent: <strong style={{ color: 'var(--text-primary)' }}>{health.metrics_24h.total_sent}</strong></span>
                  <span>24h Delivered: <strong style={{ color: '#34d399' }}>{health.metrics_24h.total_delivered}</strong></span>
                  <span>24h Failed: <strong style={{ color: health.metrics_24h.total_failed > 0 ? '#fb7185' : 'var(--text-secondary)' }}>{health.metrics_24h.total_failed}</strong></span>
                  <span>Failure Rate: <strong style={{ color: health.metrics_24h.failure_rate_percent > 5 ? '#fb7185' : 'var(--text-secondary)' }}>{health.metrics_24h.failure_rate_percent}%</strong></span>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
};
