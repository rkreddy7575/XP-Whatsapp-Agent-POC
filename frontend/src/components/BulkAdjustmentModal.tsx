import React, { useState } from 'react';
import type { StockAction } from '../types';

interface Props {
  skus: string[];
  onClose: () => void;
  onSubmit: (adjustments: Array<{ sku: string; action: StockAction; quantity: number; unit_cost?: number; notes?: string }>, reason?: string) => Promise<void>;
}

export const BulkAdjustmentModal: React.FC<Props> = ({ skus, onClose, onSubmit }) => {
  const [action, setAction] = useState<StockAction>('SET');
  const [quantity, setQuantity] = useState<string>('100');
  const [unitCost, setUnitCost] = useState<string>('');
  const [reason, setReason] = useState<string>('Bulk stock adjustment');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
        setError('Please enter a valid unit cost');
        return;
      }
      parsedCost = c;
    }

    const adjustments = skus.map((sku) => ({
      sku,
      action,
      quantity: parsedQty,
      unit_cost: parsedCost,
    }));

    try {
      setIsSubmitting(true);
      await onSubmit(adjustments, reason);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Bulk adjustment failed');
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
      id="bulk-adjust-modal"
    >
      <div
        style={{
          background: '#0f172a',
          border: '1px solid rgba(255, 255, 255, 0.15)',
          borderRadius: '12px',
          width: '100%',
          maxWidth: '500px',
          overflow: 'hidden',
        }}
      >
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
              Bulk Stock Adjustment
            </h3>
            <p style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
              Applying to {skus.length} selected SKUs
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{ background: 'transparent', border: 'none', color: '#94a3b8', fontSize: '1.25rem', cursor: 'pointer' }}
          >
            ✕
          </button>
        </div>

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

          <div style={{ marginBottom: '1rem' }}>
            <label style={{ display: 'block', fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.4rem', fontWeight: 600 }}>
              Action
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.5rem' }}>
              {(['SET', 'ADD', 'REMOVE'] as StockAction[]).map((act) => (
                <button
                  key={act}
                  type="button"
                  onClick={() => setAction(act)}
                  style={{
                    background: action === act ? '#6366f1' : 'rgba(30, 41, 59, 0.8)',
                    color: action === act ? '#ffffff' : '#cbd5e1',
                    border: '1px solid rgba(255, 255, 255, 0.1)',
                    borderRadius: '6px',
                    padding: '0.45rem',
                    fontSize: '0.8rem',
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  {act}
                </button>
              ))}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
            <div>
              <label style={{ display: 'block', fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.4rem', fontWeight: 600 }}>
                Quantity
              </label>
              <input
                type="number"
                min="0"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                required
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
                placeholder="Optional"
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

          <div style={{ marginBottom: '1.5rem' }}>
            <label style={{ display: 'block', fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.4rem', fontWeight: 600 }}>
              Reason
            </label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
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
                cursor: 'pointer',
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
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
              {isSubmitting ? 'Applying...' : `Apply to ${skus.length} SKUs`}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
