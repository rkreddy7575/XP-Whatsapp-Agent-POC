import React, { useState } from 'react';
import type { Order, OrderStatus } from '../types';
import { StatusBadge } from './StatusBadge';

interface OrderDetailsModalProps {
  order: Order | null;
  onClose: () => void;
  onUpdateStatus: (orderId: string, newStatus: OrderStatus) => Promise<void>;
}

export const OrderDetailsModal: React.FC<OrderDetailsModalProps> = ({
  order,
  onClose,
  onUpdateStatus,
}) => {
  if (!order) return null;

  const [selectedStatus, setSelectedStatus] = useState<OrderStatus>(order.status);
  const [updating, setUpdating] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      minimumFractionDigits: 2,
    }).format(amount);
  };

  const formatDateTime = (isoString?: string) => {
    if (!isoString) return '-';
    try {
      const d = new Date(isoString);
      return d.toLocaleString('en-IN', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoString;
    }
  };

  const handleStatusSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setUpdating(true);
    setSuccessMsg(null);
    setErrorMsg(null);

    try {
      await onUpdateStatus(order.order_id, selectedStatus);
      setSuccessMsg(`Order status successfully updated to ${selectedStatus}`);
    } catch (err: unknown) {
      if (err instanceof Error) {
        setErrorMsg(err.message);
      } else {
        setErrorMsg('Failed to update status.');
      }
    } finally {
      setUpdating(false);
    }
  };

  const allowedStatuses: OrderStatus[] = [
    'CONFIRMED',
    'PROCESSING',
    'READY_FOR_DISPATCH',
    'DISPATCHED',
    'DELIVERED',
    'CANCELLED',
  ];

  return (
    <div className="modal-backdrop" id="order-details-backdrop" onClick={onClose}>
      <div
        className="modal-drawer"
        id="order-details-drawer"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="modal-header">
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <h2 style={{ fontSize: '1.25rem', fontWeight: 700 }} id="detail-order-id">
                {order.order_id}
              </h2>
              <StatusBadge status={order.status} />
            </div>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.8125rem', marginTop: '0.25rem' }}>
              Created: {formatDateTime(order.created_at)}
            </p>
          </div>
          <button
            id="close-details-btn"
            className="modal-close-btn"
            onClick={onClose}
            title="Close details"
          >
            ✕
          </button>
        </div>

        {/* Customer Information */}
        <div className="detail-section">
          <div className="section-title">Customer Information</div>
          <div className="info-grid">
            <div className="info-item">
              <span className="info-label">Customer Name</span>
              <div className="info-value" id="detail-customer-name">
                {order.customer_name || 'WhatsApp Customer'}
              </div>
            </div>
            <div className="info-item">
              <span className="info-label">WhatsApp Number</span>
              <div className="info-value" id="detail-customer-phone">
                +{order.customer_phone}
              </div>
            </div>
          </div>
        </div>

        {/* Items List */}
        <div className="detail-section">
          <div className="section-title">Order Items (Immutable Snapshot)</div>
          <table className="modal-items-table" id="detail-items-table">
            <thead>
              <tr>
                <th>SKU</th>
                <th>Qty</th>
                <th>Unit Price</th>
                <th>GST</th>
                <th>Line Total</th>
              </tr>
            </thead>
            <tbody>
              {order.items.map((item, idx) => (
                <tr key={`${item.sku}-${idx}`}>
                  <td style={{ fontWeight: 600, color: 'var(--accent-primary)' }}>{item.sku}</td>
                  <td>{item.quantity}</td>
                  <td>{formatCurrency(item.unit_price)}</td>
                  <td>
                    {formatCurrency(item.gst_amount)} ({item.gst_rate}%)
                  </td>
                  <td style={{ fontWeight: 600 }}>{formatCurrency(item.line_total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Financial Summary */}
        <div className="detail-section">
          <div className="section-title">Financial Summary</div>
          <div className="financial-summary-card">
            <div className="financial-row">
              <span>Subtotal (Excl. GST)</span>
              <span id="detail-subtotal">{formatCurrency(order.subtotal)}</span>
            </div>
            <div className="financial-row">
              <span>GST Amount</span>
              <span id="detail-gst">{formatCurrency(order.gst_amount)}</span>
            </div>
            <div className="financial-row total-row">
              <span>Grand Total</span>
              <span id="detail-grand-total">{formatCurrency(order.grand_total)}</span>
            </div>
          </div>
        </div>

        {/* Inventory Status */}
        <div className="detail-section">
          <div className="section-title">Inventory Status</div>
          <div className="inventory-notice" id="detail-inventory-status">
            <span>📦</span>
            <div>
              <strong>Availability confirmation required</strong>
              <div style={{ fontSize: '0.8125rem', marginTop: '0.125rem', opacity: 0.9 }}>
                Stock availability must be verified with warehouse before dispatching.
              </div>
            </div>
          </div>
        </div>

        {/* Owner Status Update Action */}
        <div className="detail-section">
          <div className="section-title">Manage Order Status</div>
          <form className="status-action-box" onSubmit={handleStatusSubmit}>
            {successMsg && (
              <div className="feedback-msg feedback-success" id="status-update-success">
                ✓ {successMsg}
              </div>
            )}
            {errorMsg && (
              <div className="feedback-msg feedback-error" id="status-update-error">
                ⚠ {errorMsg}
              </div>
            )}

            <div className="status-action-row">
              <select
                id="update-status-dropdown"
                className="status-dropdown"
                value={selectedStatus}
                onChange={(e) => setSelectedStatus(e.target.value as OrderStatus)}
                disabled={updating}
              >
                {allowedStatuses.map((st) => (
                  <option key={st} value={st}>
                    {st.replace(/_/g, ' ')}
                  </option>
                ))}
              </select>

              <button
                id="update-status-submit-btn"
                type="submit"
                className="update-status-btn"
                disabled={updating || selectedStatus === order.status}
              >
                {updating ? 'Updating...' : 'UPDATE STATUS'}
              </button>
            </div>

            <div className="immutability-note">
              🔒 <span>Financial snapshots, GST rates, and item line items cannot be modified.</span>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
};
