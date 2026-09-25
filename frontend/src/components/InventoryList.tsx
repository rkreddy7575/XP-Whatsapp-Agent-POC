import React, { useState, useEffect, useMemo } from 'react';
import type { InventoryItem } from '../types';
import { InventoryStatusBadge } from './InventoryStatusBadge';

interface InventoryListProps {
  items: InventoryItem[];
  selectedSkus: Set<string>;
  onToggleSelect: (sku: string) => void;
  onToggleSelectAll: () => void;
  onOpenAdjustModal: (item: InventoryItem) => void;
  onOpenHistoryModal: (sku: string) => void;
  isLoading?: boolean;
}

export const InventoryList: React.FC<InventoryListProps> = ({
  items,
  selectedSkus,
  onToggleSelect,
  onToggleSelectAll,
  onOpenAdjustModal,
  onOpenHistoryModal,
  isLoading = false,
}) => {
  const [pageSize, setPageSize] = useState<number>(50);
  const [currentPage, setCurrentPage] = useState<number>(1);

  // Reset to page 1 whenever items length changes
  useEffect(() => {
    setCurrentPage(1);
  }, [items.length]);

  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const validCurrentPage = Math.min(Math.max(1, currentPage), totalPages);

  const startIndex = (validCurrentPage - 1) * pageSize;
  const endIndex = Math.min(startIndex + pageSize, items.length);
  const displayedItems = useMemo(() => {
    return items.slice(startIndex, endIndex);
  }, [items, startIndex, endIndex]);

  const allSelected = items.length > 0 && items.every((i) => selectedSkus.has(i.sku));
  const pageAllSelected = displayedItems.length > 0 && displayedItems.every((i) => selectedSkus.has(i.sku));

  const pageNumbers = useMemo(() => {
    const pages: (number | string)[] = [];
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pages.push(i);
    } else {
      pages.push(1);
      if (validCurrentPage > 3) pages.push('...');
      const start = Math.max(2, validCurrentPage - 1);
      const end = Math.min(totalPages - 1, validCurrentPage + 1);
      for (let i = start; i <= end; i++) {
        pages.push(i);
      }
      if (validCurrentPage < totalPages - 2) pages.push('...');
      pages.push(totalPages);
    }
    return pages;
  }, [totalPages, validCurrentPage]);

  if (isLoading) {
    return (
      <div className="table-card" id="loading-inventory-view">
        <div className="state-container">
          <div className="spinner" />
          <p style={{ marginTop: '1rem', color: '#94a3b8' }}>Loading inventory records...</p>
        </div>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="table-card" id="empty-inventory-view">
        <div className="state-container">
          <div className="state-icon">📦</div>
          <h3>No Inventory Items Found</h3>
          <p style={{ marginTop: '0.5rem', color: '#94a3b8' }}>
            No catalogue products match your current search or filter criteria.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="table-card" id="inventory-table-container" style={{ overflow: 'hidden' }}>
      {/* Top Pagination & Info Bar */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '0.75rem 1rem',
          background: 'rgba(15, 23, 42, 0.4)',
          borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
          flexWrap: 'wrap',
          gap: '0.75rem',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <span style={{ fontSize: '0.85rem', color: '#94a3b8' }}>
            Showing <strong style={{ color: '#f8fafc' }}>{items.length === 0 ? 0 : startIndex + 1}</strong>–
            <strong style={{ color: '#f8fafc' }}>{endIndex}</strong> of{' '}
            <strong style={{ color: '#f8fafc' }}>{items.length}</strong> SKUs
          </span>
          {selectedSkus.size > 0 && (
            <span
              style={{
                fontSize: '0.8rem',
                background: 'rgba(99, 102, 241, 0.15)',
                color: '#a5b4fc',
                padding: '0.2rem 0.6rem',
                borderRadius: '9999px',
                border: '1px solid rgba(99, 102, 241, 0.3)',
              }}
            >
              {selectedSkus.size} selected
            </span>
          )}
        </div>

        {/* Page Size & Quick Navigation */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '1.25rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <label htmlFor="page-size-select" style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
              Rows per page:
            </label>
            <select
              id="page-size-select"
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setCurrentPage(1);
              }}
              style={{
                background: 'rgba(30, 41, 59, 0.8)',
                color: '#f8fafc',
                border: '1px solid rgba(255, 255, 255, 0.15)',
                borderRadius: '6px',
                padding: '0.25rem 0.5rem',
                fontSize: '0.8rem',
                cursor: 'pointer',
                outline: 'none',
              }}
            >
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={200}>200</option>
            </select>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
            <button
              type="button"
              disabled={validCurrentPage <= 1}
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              style={{
                background: validCurrentPage <= 1 ? 'rgba(255, 255, 255, 0.02)' : 'rgba(255, 255, 255, 0.06)',
                border: '1px solid rgba(255, 255, 255, 0.1)',
                color: validCurrentPage <= 1 ? '#475569' : '#cbd5e1',
                padding: '0.3rem 0.6rem',
                borderRadius: '4px',
                fontSize: '0.78rem',
                cursor: validCurrentPage <= 1 ? 'not-allowed' : 'pointer',
              }}
            >
              &lsaquo; Prev
            </button>
            <span style={{ fontSize: '0.8rem', color: '#cbd5e1', padding: '0 0.4rem' }}>
              Page <strong>{validCurrentPage}</strong> of <strong>{totalPages}</strong>
            </span>
            <button
              type="button"
              disabled={validCurrentPage >= totalPages}
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              style={{
                background: validCurrentPage >= totalPages ? 'rgba(255, 255, 255, 0.02)' : 'rgba(255, 255, 255, 0.06)',
                border: '1px solid rgba(255, 255, 255, 0.1)',
                color: validCurrentPage >= totalPages ? '#475569' : '#cbd5e1',
                padding: '0.3rem 0.6rem',
                borderRadius: '4px',
                fontSize: '0.78rem',
                cursor: validCurrentPage >= totalPages ? 'not-allowed' : 'pointer',
              }}
            >
              Next &rsaquo;
            </button>
          </div>
        </div>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table className="inventory-table" id="inventory-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr
              style={{
                background: 'rgba(15, 23, 42, 0.8)',
                borderBottom: '1px solid rgba(255, 255, 255, 0.1)',
                color: '#94a3b8',
                fontSize: '0.75rem',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
              }}
            >
              <th style={{ padding: '0.85rem 1rem', width: '40px' }}>
                <input
                  type="checkbox"
                  checked={allSelected || pageAllSelected}
                  onChange={onToggleSelectAll}
                  aria-label="Select all SKUs"
                  id="inv-select-all-header"
                />
              </th>
              <th style={{ padding: '0.85rem 1rem' }}>Product & SKU</th>
              <th style={{ padding: '0.85rem 1rem' }}>Category</th>
              <th style={{ padding: '0.85rem 1rem', textAlign: 'right' }}>Physical</th>
              <th style={{ padding: '0.85rem 1rem', textAlign: 'right' }}>Reserved</th>
              <th style={{ padding: '0.85rem 1rem', textAlign: 'right' }}>Available</th>
              <th style={{ padding: '0.85rem 1rem', textAlign: 'right' }}>Reorder Lvl</th>
              <th style={{ padding: '0.85rem 1rem', textAlign: 'right' }}>Imported Cost</th>
              <th style={{ padding: '0.85rem 1rem' }}>Status</th>
              <th style={{ padding: '0.85rem 1rem', textAlign: 'center' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {displayedItems.map((item) => {
              const isChecked = selectedSkus.has(item.sku);
              const phys = item.physical_stock;
              const res = item.reserved_stock;
              const avail = item.available_stock;

              return (
                <tr
                  key={item.sku}
                  style={{
                    borderBottom: '1px solid rgba(255, 255, 255, 0.05)',
                    background: isChecked ? 'rgba(99, 102, 241, 0.08)' : 'transparent',
                    transition: 'background 0.15s ease',
                  }}
                  className="inventory-table-row"
                >
                  <td style={{ padding: '0.85rem 1rem' }}>
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={() => onToggleSelect(item.sku)}
                      id={`inv-select-${item.sku}`}
                    />
                  </td>

                  {/* SKU & Name */}
                  <td style={{ padding: '0.85rem 1rem' }}>
                    <div style={{ fontWeight: 600, color: '#f8fafc', fontSize: '0.9rem' }}>
                      {item.sku}
                    </div>
                    <div style={{ color: '#94a3b8', fontSize: '0.78rem' }}>
                      {item.name || '—'}
                    </div>
                  </td>

                  {/* Category */}
                  <td style={{ padding: '0.85rem 1rem', color: '#cbd5e1' }}>
                    <span
                      style={{
                        background: 'rgba(255, 255, 255, 0.06)',
                        padding: '0.2rem 0.5rem',
                        borderRadius: '4px',
                        fontSize: '0.75rem',
                      }}
                    >
                      {item.category || '—'}
                    </span>
                  </td>

                  {/* Physical Stock */}
                  <td style={{ padding: '0.85rem 1rem', textAlign: 'right', fontWeight: 600 }}>
                    {phys !== null ? (
                      <span style={{ color: phys === 0 ? '#fb7185' : '#f8fafc' }}>
                        {phys.toLocaleString()}
                      </span>
                    ) : (
                      <span style={{ color: '#64748b' }}>—</span>
                    )}
                  </td>

                  {/* Reserved Stock */}
                  <td style={{ padding: '0.85rem 1rem', textAlign: 'right', color: res > 0 ? '#fbbf24' : '#64748b' }}>
                    {res.toLocaleString()}
                  </td>

                  {/* Available Stock */}
                  <td style={{ padding: '0.85rem 1rem', textAlign: 'right', fontWeight: 700 }}>
                    {avail !== null ? (
                      <span
                        style={{
                          color:
                            avail === 0
                              ? '#fb7185'
                              : avail <= item.reorder_level
                              ? '#fbbf24'
                              : '#34d399',
                        }}
                      >
                        {avail.toLocaleString()}
                      </span>
                    ) : (
                      <span style={{ color: '#64748b' }}>—</span>
                    )}
                  </td>

                  {/* Reorder Level */}
                  <td style={{ padding: '0.85rem 1rem', textAlign: 'right', color: '#94a3b8' }}>
                    {item.reorder_level.toLocaleString()}
                  </td>

                  {/* Imported Unit Cost */}
                  <td style={{ padding: '0.85rem 1rem', textAlign: 'right', color: item.unit_cost !== null ? '#94a3b8' : '#64748b' }}>
                    {item.unit_cost !== null ? `₹${item.unit_cost.toFixed(2)}` : '—'}
                  </td>

                  {/* Status Badge */}
                  <td style={{ padding: '0.85rem 1rem' }}>
                    <InventoryStatusBadge status={item.status} />
                  </td>

                  {/* Actions */}
                  <td style={{ padding: '0.85rem 1rem', textAlign: 'center' }}>
                    <div style={{ display: 'flex', gap: '0.4rem', justifyContent: 'center' }}>
                      <button
                        type="button"
                        onClick={() => onOpenAdjustModal(item)}
                        id={`btn-adjust-${item.sku}`}
                        style={{
                          background: 'rgba(99, 102, 241, 0.15)',
                          border: '1px solid rgba(99, 102, 241, 0.3)',
                          color: '#818cf8',
                          padding: '0.3rem 0.55rem',
                          borderRadius: '4px',
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          cursor: 'pointer',
                        }}
                        title="Adjust physical stock or unit cost"
                      >
                        Adjust
                      </button>

                      <button
                        type="button"
                        onClick={() => onOpenHistoryModal(item.sku)}
                        id={`btn-history-${item.sku}`}
                        style={{
                          background: 'rgba(255, 255, 255, 0.06)',
                          border: '1px solid rgba(255, 255, 255, 0.12)',
                          color: '#94a3b8',
                          padding: '0.3rem 0.55rem',
                          borderRadius: '4px',
                          fontSize: '0.75rem',
                          cursor: 'pointer',
                        }}
                        title="View audit transaction logs"
                      >
                        History
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Bottom Pagination Controls Bar */}
      <div
        style={{
          padding: '0.85rem 1.25rem',
          background: 'rgba(15, 23, 42, 0.7)',
          borderTop: '1px solid rgba(255, 255, 255, 0.06)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '1rem',
        }}
      >
        <div style={{ fontSize: '0.82rem', color: '#64748b' }}>
          Showing <strong>{items.length === 0 ? 0 : startIndex + 1}</strong> to <strong>{endIndex}</strong> of{' '}
          <strong>{items.length}</strong> catalogue records
        </div>

        {/* Page Buttons List */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
          <button
            type="button"
            disabled={validCurrentPage <= 1}
            onClick={() => setCurrentPage(1)}
            style={{
              background: validCurrentPage <= 1 ? 'rgba(255, 255, 255, 0.02)' : 'rgba(255, 255, 255, 0.06)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              color: validCurrentPage <= 1 ? '#475569' : '#94a3b8',
              padding: '0.35rem 0.65rem',
              borderRadius: '6px',
              fontSize: '0.8rem',
              cursor: validCurrentPage <= 1 ? 'not-allowed' : 'pointer',
            }}
            title="First Page"
          >
            &laquo; First
          </button>

          <button
            type="button"
            disabled={validCurrentPage <= 1}
            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
            style={{
              background: validCurrentPage <= 1 ? 'rgba(255, 255, 255, 0.02)' : 'rgba(255, 255, 255, 0.06)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              color: validCurrentPage <= 1 ? '#475569' : '#94a3b8',
              padding: '0.35rem 0.65rem',
              borderRadius: '6px',
              fontSize: '0.8rem',
              cursor: validCurrentPage <= 1 ? 'not-allowed' : 'pointer',
            }}
            title="Previous Page"
          >
            &lsaquo; Prev
          </button>

          {pageNumbers.map((page, idx) => {
            if (page === '...') {
              return (
                <span key={`ellipsis-${idx}`} style={{ color: '#64748b', padding: '0 0.25rem' }}>
                  ...
                </span>
              );
            }
            const isCurrent = page === validCurrentPage;
            return (
              <button
                key={`page-${page}`}
                type="button"
                onClick={() => setCurrentPage(Number(page))}
                style={{
                  background: isCurrent ? 'linear-gradient(135deg, #6366f1, #4f46e5)' : 'rgba(255, 255, 255, 0.05)',
                  border: isCurrent ? '1px solid #818cf8' : '1px solid rgba(255, 255, 255, 0.1)',
                  color: isCurrent ? '#ffffff' : '#cbd5e1',
                  fontWeight: isCurrent ? 700 : 500,
                  minWidth: '32px',
                  height: '32px',
                  borderRadius: '6px',
                  fontSize: '0.82rem',
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
              >
                {page}
              </button>
            );
          })}

          <button
            type="button"
            disabled={validCurrentPage >= totalPages}
            onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
            style={{
              background: validCurrentPage >= totalPages ? 'rgba(255, 255, 255, 0.02)' : 'rgba(255, 255, 255, 0.06)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              color: validCurrentPage >= totalPages ? '#475569' : '#94a3b8',
              padding: '0.35rem 0.65rem',
              borderRadius: '6px',
              fontSize: '0.8rem',
              cursor: validCurrentPage >= totalPages ? 'not-allowed' : 'pointer',
            }}
            title="Next Page"
          >
            Next &rsaquo;
          </button>

          <button
            type="button"
            disabled={validCurrentPage >= totalPages}
            onClick={() => setCurrentPage(totalPages)}
            style={{
              background: validCurrentPage >= totalPages ? 'rgba(255, 255, 255, 0.02)' : 'rgba(255, 255, 255, 0.06)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              color: validCurrentPage >= totalPages ? '#475569' : '#94a3b8',
              padding: '0.35rem 0.65rem',
              borderRadius: '6px',
              fontSize: '0.8rem',
              cursor: validCurrentPage >= totalPages ? 'not-allowed' : 'pointer',
            }}
            title="Last Page"
          >
            Last &raquo;
          </button>
        </div>
      </div>
    </div>
  );
};
