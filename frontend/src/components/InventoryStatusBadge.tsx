import React from 'react';
import type { InventoryStatus } from '../types';

interface Props {
  status: InventoryStatus;
}

export const InventoryStatusBadge: React.FC<Props> = ({ status }) => {
  const getStyle = () => {
    switch (status) {
      case 'IN_STOCK':
        return {
          background: 'rgba(16, 185, 129, 0.15)',
          color: '#34d399',
          border: '1px solid rgba(16, 185, 129, 0.35)',
        };
      case 'LOW_STOCK':
        return {
          background: 'rgba(245, 158, 11, 0.15)',
          color: '#fbbf24',
          border: '1px solid rgba(245, 158, 11, 0.35)',
        };
      case 'OUT_OF_STOCK':
        return {
          background: 'rgba(244, 63, 94, 0.15)',
          color: '#fb7185',
          border: '1px solid rgba(244, 63, 94, 0.35)',
        };
      case 'COMING_SOON':
        return {
          background: 'rgba(6, 182, 212, 0.15)',
          color: '#22d3ee',
          border: '1px solid rgba(6, 182, 212, 0.35)',
        };
      case 'SUPPLIER_CONFIRMATION_REQUIRED':
        return {
          background: 'rgba(168, 85, 247, 0.15)',
          color: '#c084fc',
          border: '1px solid rgba(168, 85, 247, 0.35)',
        };
      case 'UNKNOWN':
      default:
        return {
          background: 'rgba(148, 163, 184, 0.15)',
          color: '#94a3b8',
          border: '1px solid rgba(148, 163, 184, 0.35)',
        };
    }
  };

  const getLabel = () => {
    switch (status) {
      case 'IN_STOCK': return 'In Stock';
      case 'LOW_STOCK': return 'Low Stock';
      case 'OUT_OF_STOCK': return 'Out of Stock';
      case 'COMING_SOON': return 'Coming Soon';
      case 'SUPPLIER_CONFIRMATION_REQUIRED': return 'Supplier Confirmation';
      case 'UNKNOWN':
      default:
        return 'Unknown';
    }
  };

  return (
    <span
      style={{
        ...getStyle(),
        padding: '0.25rem 0.6rem',
        borderRadius: '9999px',
        fontSize: '0.75rem',
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.025em',
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.35rem',
      }}
    >
      <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'currentColor' }} />
      {getLabel()}
    </span>
  );
};
