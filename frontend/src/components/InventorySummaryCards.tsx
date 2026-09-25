import React from 'react';
import type { InventorySummary } from '../types';

interface Props {
  summary: InventorySummary | null;
  activeStatus: string;
  stockAttentionOnly: boolean;
  onFilterStatus: (status: string) => void;
  onToggleStockAttention: () => void;
}

export const InventorySummaryCards: React.FC<Props> = ({
  summary,
  activeStatus,
  stockAttentionOnly,
  onFilterStatus,
  onToggleStockAttention,
}) => {
  if (!summary) return null;

  const cards = [
    {
      id: 'total',
      label: 'Total SKUs',
      count: summary.total_skus,
      status: 'ALL',
      color: '#6366f1',
      bg: 'rgba(99, 102, 241, 0.1)',
      border: 'rgba(99, 102, 241, 0.3)',
      icon: '📦',
      desc: 'Authoritative catalogue items',
    },
    {
      id: 'in_stock',
      label: 'In Stock',
      count: summary.in_stock,
      status: 'IN_STOCK',
      color: '#34d399',
      bg: 'rgba(16, 185, 129, 0.1)',
      border: 'rgba(16, 185, 129, 0.3)',
      icon: '✅',
      desc: 'Available > Reorder level',
    },
    {
      id: 'low_stock',
      label: 'Low Stock',
      count: summary.low_stock,
      status: 'LOW_STOCK',
      color: '#fbbf24',
      bg: 'rgba(245, 158, 11, 0.1)',
      border: 'rgba(245, 158, 11, 0.3)',
      icon: '⚠️',
      desc: 'Available ≤ Reorder level',
    },
    {
      id: 'out_of_stock',
      label: 'Out of Stock',
      count: summary.out_of_stock,
      status: 'OUT_OF_STOCK',
      color: '#fb7185',
      bg: 'rgba(244, 63, 94, 0.1)',
      border: 'rgba(244, 63, 94, 0.3)',
      icon: '🚫',
      desc: 'Physical/available is 0',
    },
    {
      id: 'unknown',
      label: 'Unknown Stock',
      count: summary.unknown,
      status: 'UNKNOWN',
      color: '#94a3b8',
      bg: 'rgba(148, 163, 184, 0.1)',
      border: 'rgba(148, 163, 184, 0.3)',
      icon: '❓',
      desc: 'Needs warehouse count',
    },
    {
      id: 'attention',
      label: 'Stock Attention',
      count: summary.stock_attention_count,
      status: 'ATTENTION',
      color: '#f43f5e',
      bg: 'rgba(244, 63, 94, 0.15)',
      border: 'rgba(244, 63, 94, 0.4)',
      icon: '🚨',
      desc: 'Low + Out + Unknown',
      isAttentionToggle: true,
    },
  ];

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
        gap: '1rem',
        marginBottom: '1.5rem',
      }}
      id="inventory-summary-cards"
    >
      {cards.map((c) => {
        const isSelected = c.isAttentionToggle
          ? stockAttentionOnly
          : activeStatus === c.status && !stockAttentionOnly;

        return (
          <div
            key={c.id}
            onClick={() => {
              if (c.isAttentionToggle) {
                onToggleStockAttention();
              } else {
                onFilterStatus(c.status);
              }
            }}
            style={{
              background: isSelected ? c.bg : 'rgba(23, 32, 54, 0.75)',
              border: `1px solid ${isSelected ? c.color : 'rgba(255, 255, 255, 0.08)'}`,
              borderRadius: '10px',
              padding: '1.1rem 1.25rem',
              cursor: 'pointer',
              transition: 'all 0.2s ease',
              boxShadow: isSelected ? `0 0 15px ${c.bg}` : 'none',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.4rem' }}>
              <span style={{ fontSize: '0.85rem', color: '#94a3b8', fontWeight: 500 }}>{c.label}</span>
              <span style={{ fontSize: '1.1rem' }}>{c.icon}</span>
            </div>
            <div style={{ fontSize: '1.75rem', fontWeight: 700, color: c.color, marginBottom: '0.2rem' }}>
              {c.count.toLocaleString()}
            </div>
            <div style={{ fontSize: '0.72rem', color: '#64748b' }}>{c.desc}</div>
          </div>
        );
      })}
    </div>
  );
};
