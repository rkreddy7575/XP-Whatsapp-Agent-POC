import React, { useState } from 'react';
import type { InventoryItem, StockAction } from '../types';

interface Props {
  item: InventoryItem | null;
  onClose: () => void;
  onSubmit: (sku: string, action: StockAction, quantity: number, unitCost?: number, reason?: string, notes?: string) => Promise<void>;
}

export const StockAdjustmentModal: React.FC<Props> = ({ item, onClose, onSubmit }) => {
  const [action, setAction] = useState<StockAction>('SET');
  const [quantity, setQuantity] = useState<string>(item?.physical_stock !== null && item?.physical_stock !== undefined ? String(item.physical_stock) : '0');
  const [unitCost, setUnitCost] = useState<string>(item?.unit_cost !== null && item?.unit_cost !== undefined ? String(item.unit_cost) : '');
  const [reason, setReason] = useState<string>('');
  const [notes, setNotes] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!item) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const parsedQty = parseInt(quantity, 10);
    if (isNaN(parsedQty) || parsedQty < 0) {
      setError('Please enter a valid non-negative quantity');
      return;
    }

    let parsedCost: number | undefined = undefined;
    if (unitCost.trim()) {
      const c = parseFloat(unitCost);
      if (isNaN(c) || c < 0) {
        setError('Please enter a valid non-negative unit cost');
        return;
      }
      parsedCost = c;
    }

    try {
      setIsSubmitting(true);
      await onSubmit(item.sku, action, parsedQty, parsedCost, reason, notes);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to adjust stock');
    } finally {
      setIsSubmitting(false);
    }
  };

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
      id="stock-adjustment-modal"
    >
      <div
        style={{
          background: '#0f172a',
          border: '1px solid rgba(255, 255, 255, 0.15)',
          borderRadius: '12px',
          width: '100%',
          maxWidth: '520px',
          overflow: 'hidden',
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
              Adjust Stock — {item.sku}
            </h3>
            <p style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
              {item.name || item.category || 'Product SKU'}
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

        {/* Current stats bar */}
        <div
          style={{
            background: 'rgba(30, 41, 59, 0.5)',
            padding: '0.75rem 1.5rem',
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: '0.8rem',
            borderBottom: '1px solid rgba(255, 255, 255, 0.05)',
          }}
        >
          <div>
            <span style={{ color: '#94a3b8' }}>Current Physical: </span>
            <strong style={{ color: '#f8fafc' }}>{item.physical_stock !== null ? item.physical_stock : 'UNKNOWN'}</strong>
          </div>
          <div>
            <span style={{ color: '#94a3b8' }}>Reserved: </span>
            <strong style={{ color: '#fbbf24' }}>{item.reserved_stock}</strong>
          </div>
          <div>
            <span style={{ color: '#94a3b8' }}>Available: </span>
            <strong style={{ color: '#34d399' }}>{item.available_stock !== null ? item.available_stock : 'UNKNOWN'}</strong>
          </div>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} style={{ padding: '1.5rem' }}>
          {error && (
            <div
              style={{
                background: 'rgba(244, 63, 94, 0.15)',
                border: '1px solid rgba(244, 63, 94, 0.3)',
                color: '#fb7185',
                padding: '0.75rem',
                borderRadius: '6px',
                fontSize: '0.85rem',
                marginBottom: '1rem',
              }}
            >
              {error}
            </div>
          )}

          {/* Action Type */}
          <div style={{ marginBottom: '1rem' }}>
            <label style={{ display: 'block', fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.4rem', fontWeight: 600 }}>
              Adjustment Action
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0.5rem' }}>
              {(['SET', 'ADD', 'REMOVE', 'DAMAGED'] as StockAction[]).map((act) => (
                <button
                  key={act}
                  type="button"
                  onClick={() => setAction(act)}
                  style={{
                    background: action === act ? '#6366f1' : 'rgba(30, 41, 59, 0.8)',
                    color: action === act ? '#ffffff' : '#cbd5e1',
                    border: '1px solid rgba(255, 255, 255, 0.1)',
                    borderRadius: '6px',
                    padding: '0.45rem 0.2rem',
                    fontSize: '0.78rem',
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  {act}
                </button>
              ))}
            </div>
          </div>

          {/* Quantity & Unit Cost */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
            <div>
              <label style={{ display: 'block', fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.4rem', fontWeight: 600 }}>
                {action === 'SET' ? 'New Total Quantity' : 'Units to ' + action}
              </label>
              <input
                type="number"
                min="0"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                required
                id="adjust-qty-input"
                style={{
                  width: '100%',
                  background: 'rgba(15, 23, 42, 0.8)',
                  border: '1px solid rgba(255, 255, 255, 0.15)',
                  borderRadius: '6px',
                  color: '#f8fafc',
                  padding: '0.55rem 0.75rem',
                  fontSize: '0.9rem',
                }}
              />
            </div>

            <div>
              <label style={{ display: 'block', fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.4rem', fontWeight: 600 }}>
                Imported Cost (₹)
              </label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={unitCost}
                onChange={(e) => setUnitCost(e.target.value)}
                placeholder="e.g. 75.00"
                id="adjust-cost-input"
                style={{
                  width: '100%',
                  background: 'rgba(15, 23, 42, 0.8)',
                  border: '1px solid rgba(255, 255, 255, 0.15)',
                  borderRadius: '6px',
                  color: '#f8fafc',
                  padding: '0.55rem 0.75rem',
                  fontSize: '0.9rem',
                }}
              />
            </div>
          </div>

          {/* Reason */}
          <div style={{ marginBottom: '1rem' }}>
            <label style={{ display: 'block', fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.4rem', fontWeight: 600 }}>
              Reason for Adjustment
            </label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Physical inventory count / Warehouse intake"
              id="adjust-reason-input"
              style={{
                width: '100%',
                background: 'rgba(15, 23, 42, 0.8)',
                border: '1px solid rgba(255, 255, 255, 0.15)',
                borderRadius: '6px',
                color: '#f8fafc',
                padding: '0.55rem 0.75rem',
                fontSize: '0.85rem',
              }}
            />
          </div>

          {/* Notes */}
          <div style={{ marginBottom: '1.5rem' }}>
            <label style={{ display: 'block', fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.4rem' }}>
              Additional Notes (Optional)
            </label>
            <textarea
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Internal tracking notes..."
              id="adjust-notes-input"
              style={{
                width: '100%',
                background: 'rgba(15, 23, 42, 0.8)',
                border: '1px solid rgba(255, 255, 255, 0.15)',
                borderRadius: '6px',
                color: '#f8fafc',
                padding: '0.55rem 0.75rem',
                fontSize: '0.85rem',
                resize: 'none',
              }}
            />
          </div>

          {/* Footer buttons */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.75rem' }}>
            <button
              type="button"
              onClick={onClose}
              style={{
                background: 'transparent',
                border: '1px solid rgba(255, 255, 255, 0.2)',
                color: '#94a3b8',
                padding: '0.55rem 1rem',
                borderRadius: '6px',
                fontSize: '0.85rem',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              id="adjust-submit-btn"
              style={{
                background: '#6366f1',
                border: 'none',
                color: '#ffffff',
                padding: '0.55rem 1.25rem',
                borderRadius: '6px',
                fontSize: '0.85rem',
                fontWeight: 600,
                cursor: isSubmitting ? 'not-allowed' : 'pointer',
              }}
            >
              {isSubmitting ? 'Saving...' : 'Apply Adjustment'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
