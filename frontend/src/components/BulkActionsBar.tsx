import React from 'react';

interface Props {
  selectedCount: number;
  onBulkAdjust: () => void;
  onBulkStatus: () => void;
  onBulkReorder: () => void;
  onClearSelection: () => void;
}

export const BulkActionsBar: React.FC<Props> = ({
  selectedCount,
  onBulkAdjust,
  onBulkStatus,
  onBulkReorder,
  onClearSelection,
}) => {
  if (selectedCount === 0) return null;

  return (
    <div
      style={{
        position: 'sticky',
        bottom: '1.5rem',
        zIndex: 50,
        background: '#1e293b',
        border: '1px solid #6366f1',
        borderRadius: '10px',
        padding: '0.85rem 1.25rem',
        boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '0.75rem',
        marginTop: '1rem',
      }}
      id="inv-bulk-actions-bar"
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
        <span
          style={{
            background: '#6366f1',
            color: '#ffffff',
            padding: '0.2rem 0.65rem',
            borderRadius: '9999px',
            fontSize: '0.8rem',
            fontWeight: 700,
          }}
        >
          {selectedCount} Selected
        </span>
        <span style={{ fontSize: '0.85rem', color: '#cbd5e1' }}>
          Bulk inventory operations available:
        </span>
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <button
          type="button"
          onClick={onBulkAdjust}
          id="bulk-adjust-btn"
          style={{
            background: 'rgba(99, 102, 241, 0.2)',
            border: '1px solid rgba(99, 102, 241, 0.4)',
            color: '#a5b4fc',
            padding: '0.4rem 0.75rem',
            borderRadius: '6px',
            fontSize: '0.8rem',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          ✏️ Bulk Stock Adjust
        </button>

        <button
          type="button"
          onClick={onBulkStatus}
          id="bulk-status-btn"
          style={{
            background: 'rgba(168, 85, 247, 0.2)',
            border: '1px solid rgba(168, 85, 247, 0.4)',
            color: '#d8b4fe',
            padding: '0.4rem 0.75rem',
            borderRadius: '6px',
            fontSize: '0.8rem',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          🏷️ Change Status
        </button>

        <button
          type="button"
          onClick={onBulkReorder}
          id="bulk-reorder-btn"
          style={{
            background: 'rgba(245, 158, 11, 0.2)',
            border: '1px solid rgba(245, 158, 11, 0.4)',
            color: '#fcd34d',
            padding: '0.4rem 0.75rem',
            borderRadius: '6px',
            fontSize: '0.8rem',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          🔔 Set Reorder Level
        </button>

        <button
          type="button"
          onClick={onClearSelection}
          id="bulk-clear-btn"
          style={{
            background: 'transparent',
            border: '1px solid rgba(255, 255, 255, 0.2)',
            color: '#94a3b8',
            padding: '0.4rem 0.75rem',
            borderRadius: '6px',
            fontSize: '0.8rem',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          ✕ Deselect All
        </button>
      </div>
    </div>
  );
};
