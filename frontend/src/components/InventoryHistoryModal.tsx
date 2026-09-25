import React, { useEffect, useState } from 'react';
import type { InventoryTransaction } from '../types';
import { fetchInventoryHistory } from '../api';

interface Props {
  sku: string | null;
  onClose: () => void;
}

export const InventoryHistoryModal: React.FC<Props> = ({ sku, onClose }) => {
  const [history, setHistory] = useState<InventoryTransaction[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sku) return;
    setIsLoading(true);
    fetchInventoryHistory(sku)
      .then((data) => setHistory(data))
      .catch((err) => setError(err.message))
      .finally(() => setIsLoading(false));
  }, [sku]);

  if (!sku) return null;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0, 0, 0, 0.75)',
        backdropFilter: 'blur(4px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
        padding: '1rem',
      }}
      id="inventory-history-modal"
    >
      <div
        style={{
          background: '#0f172a',
          border: '1px solid rgba(255, 255, 255, 0.15)',
          borderRadius: '12px',
          width: '100%',
          maxWidth: '680px',
          maxHeight: '85vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '1.25rem 1.5rem',
            borderBottom: '1px solid rgba(255, 255, 255, 0.1)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <div>
            <h3 style={{ fontSize: '1.1rem', fontWeight: 700, color: '#f8fafc' }}>
              Transaction Audit Trail — {sku}
            </h3>
            <p style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
              Immutable audit history of all physical stock adjustments
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#94a3b8',
              fontSize: '1.25rem',
              cursor: 'pointer',
            }}
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div style={{ padding: '1.5rem', overflowY: 'auto', flex: 1 }}>
          {isLoading && (
            <div style={{ textAlign: 'center', color: '#94a3b8', padding: '2rem' }}>
              Loading audit transactions...
            </div>
          )}

          {error && (
            <div
              style={{
                background: 'rgba(244, 63, 94, 0.15)',
                border: '1px solid rgba(244, 63, 94, 0.3)',
                color: '#fb7185',
                padding: '0.75rem',
                borderRadius: '6px',
                fontSize: '0.85rem',
              }}
            >
              {error}
            </div>
          )}

          {!isLoading && !error && history.length === 0 && (
            <div style={{ textAlign: 'center', color: '#94a3b8', padding: '2rem' }}>
              No audit transactions recorded yet for SKU {sku}.
            </div>
          )}

          {!isLoading && !error && history.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {history.map((tx) => {
                const change = tx.quantity_change;
                const isPos = change > 0;
                const isNeg = change < 0;

                return (
                  <div
                    key={tx.id}
                    style={{
                      background: 'rgba(30, 41, 59, 0.5)',
                      border: '1px solid rgba(255, 255, 255, 0.05)',
                      borderRadius: '8px',
                      padding: '0.85rem 1rem',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.35rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span
                          style={{
                            background: 'rgba(99, 102, 241, 0.15)',
                            color: '#818cf8',
                            padding: '0.15rem 0.45rem',
                            borderRadius: '4px',
                            fontSize: '0.72rem',
                            fontWeight: 700,
                          }}
                        >
                          {tx.transaction_type}
                        </span>
                        <span style={{ fontSize: '0.85rem', fontWeight: 600, color: isPos ? '#34d399' : isNeg ? '#fb7185' : '#94a3b8' }}>
                          {isPos ? `+${change}` : change} units
                        </span>
                      </div>
                      <span style={{ fontSize: '0.75rem', color: '#64748b' }}>
                        {new Date(tx.created_at).toLocaleString()}
                      </span>
                    </div>

                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem', color: '#cbd5e1' }}>
                      <div>
                        <span style={{ color: '#94a3b8' }}>Stock Level: </span>
                        <span>{tx.quantity_before !== null ? tx.quantity_before : '—'}</span>
                        <span> → </span>
                        <strong>{tx.quantity_after !== null ? tx.quantity_after : '—'}</strong>
                      </div>
                      <div style={{ color: '#94a3b8' }}>By: {tx.created_by}</div>
                    </div>

                    {tx.reason && (
                      <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginTop: '0.35rem' }}>
                        Reason: {tx.reason}
                      </div>
                    )}
                    {tx.notes && (
                      <div style={{ fontSize: '0.72rem', color: '#64748b', marginTop: '0.2rem', fontStyle: 'italic' }}>
                        Note: {tx.notes}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '1rem 1.5rem',
            borderTop: '1px solid rgba(255, 255, 255, 0.1)',
            display: 'flex',
            justifyContent: 'flex-end',
          }}
        >
          <button
            type="button"
            onClick={onClose}
            style={{
              background: '#6366f1',
              border: 'none',
              color: '#ffffff',
              padding: '0.5rem 1.25rem',
              borderRadius: '6px',
              fontSize: '0.85rem',
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
