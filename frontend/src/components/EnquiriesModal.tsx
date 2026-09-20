import React from 'react';
import type { Enquiry } from '../types';

interface EnquiriesModalProps {
  isOpen: boolean;
  onClose: () => void;
  enquiries: Enquiry[];
}

export const EnquiriesModal: React.FC<EnquiriesModalProps> = ({
  isOpen,
  onClose,
  enquiries,
}) => {
  if (!isOpen) return null;

  const formatDate = (isoString?: string) => {
    if (!isoString) return '-';
    try {
      const d = new Date(isoString);
      return d.toLocaleString('en-IN', {
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoString;
    }
  };

  const formatCurrency = (amount?: number) => {
    if (amount == null) return '-';
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(amount);
  };

  return (
    <div className="modal-backdrop" id="enquiries-backdrop" onClick={onClose}>
      <div
        className="modal-drawer"
        id="enquiries-drawer"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '640px' }}
      >
        <div className="modal-header">
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <h2 style={{ fontSize: '1.25rem', fontWeight: 700 }} id="enquiries-title">
                💬 WhatsApp Leads & Active Enquiries
              </h2>
            </div>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.8125rem', marginTop: '0.25rem' }}>
              Real-time multi-turn conversations & pending quotations in sales funnel
            </p>
          </div>
          <button
            id="close-enquiries-btn"
            className="modal-close-btn"
            onClick={onClose}
            title="Close"
          >
            ✕
          </button>
        </div>

        {enquiries.length === 0 ? (
          <div className="state-container" style={{ padding: '3rem 1rem' }}>
            <div className="state-icon">💬</div>
            <h3>No Active Leads</h3>
            <p style={{ marginTop: '0.5rem', color: 'var(--text-secondary)' }}>
              All customer enquiries have been converted to orders or are waiting for new interactions.
            </p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginTop: '1rem' }}>
            {enquiries.map((enq) => (
              <div
                key={enq.conversation_id}
                style={{
                  background: 'var(--bg-secondary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-md)',
                  padding: '1rem',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <span style={{ fontWeight: 600, fontSize: '0.9375rem' }}>
                      {enq.customer_name ? `${enq.customer_name} (+${enq.customer_phone})` : `+${enq.customer_phone}`}
                    </span>
                    {enq.has_pending_quote && (
                      <span
                        style={{
                          background: 'rgba(245, 158, 11, 0.15)',
                          color: '#fbbf24',
                          border: '1px solid rgba(245, 158, 11, 0.35)',
                          fontSize: '0.6875rem',
                          padding: '0.125rem 0.5rem',
                          borderRadius: '9999px',
                          fontWeight: 600,
                        }}
                      >
                        QUOTE PENDING
                      </span>
                    )}
                    {enq.latest_order_id && (
                      <span
                        style={{
                          background: 'rgba(99, 102, 241, 0.15)',
                          color: '#818cf8',
                          border: '1px solid rgba(99, 102, 241, 0.35)',
                          fontSize: '0.6875rem',
                          padding: '0.125rem 0.5rem',
                          borderRadius: '9999px',
                          fontWeight: 600,
                        }}
                      >
                        ORDERED ({enq.latest_order_id})
                      </span>
                    )}
                  </div>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                    {formatDate(enq.updated_at)}
                  </span>
                </div>

                <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                  {enq.enquiry_text && (
                    <div style={{ gridColumn: 'span 2', background: 'rgba(0,0,0,0.2)', padding: '0.4rem 0.6rem', borderRadius: '4px' }}>
                      <strong>Latest Customer Message:</strong> &ldquo;{enq.enquiry_text}&rdquo;
                    </div>
                  )}
                  <div>
                    <strong>Products Discussed:</strong>{' '}
                    {enq.products_discussed && enq.products_discussed.length > 0
                      ? enq.products_discussed.join(', ')
                      : enq.selected_sku || 'General catalogue browse'}
                  </div>
                  <div>
                    <strong>Quantity:</strong> {enq.selected_quantity ? `${enq.selected_quantity} units` : '-'}
                  </div>
                  {enq.pending_quote && (
                    <div style={{ gridColumn: 'span 2', marginTop: '0.25rem', color: '#34d399', fontWeight: 600 }}>
                      Quotation Value: {formatCurrency(enq.pending_quote.total_price_incl_gst)} (incl. GST)
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
