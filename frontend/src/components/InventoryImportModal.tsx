import React, { useState } from 'react';
import type { ImportPreviewResult } from '../types';
import { previewInventoryImport, applyInventoryImport } from '../api';

interface Props {
  onClose: () => void;
  onSuccess: () => void;
}

export const InventoryImportModal: React.FC<Props> = ({ onClose, onSuccess }) => {
  const [step, setStep] = useState<1 | 2>(1);
  const [fileText, setFileText] = useState<string>('');
  const [parsedRows, setParsedRows] = useState<Array<Record<string, any>>>([]);
  const [previewResult, setPreviewResult] = useState<ImportPreviewResult | null>(null);
  const [importMode, setImportMode] = useState<'add' | 'replace'>('add');
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      const text = event.target?.result as string;
      setFileText(text);
      parseCsv(text);
    };
    reader.readAsText(file);
  };

  const parseCsv = (text: string) => {
    setError(null);
    const lines = text.trim().split(/\r?\n/);
    if (lines.length < 2) {
      setError('CSV file must have a header row and at least one data row.');
      return;
    }

    const headers = lines[0].split(',').map((h) => h.trim().replace(/^["']|["']$/g, ''));
    const rows: Array<Record<string, any>> = [];

    for (let i = 1; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;
      const cols = line.split(',').map((c) => c.trim().replace(/^["']|["']$/g, ''));
      const rowObj: Record<string, any> = {};
      headers.forEach((h, idx) => {
        rowObj[h] = cols[idx] !== undefined ? cols[idx] : '';
      });
      rows.push(rowObj);
    }

    setParsedRows(rows);
  };

  const handleRunPreview = async () => {
    if (parsedRows.length === 0) {
      if (fileText.trim()) {
        parseCsv(fileText);
      } else {
        setError('Please upload or paste CSV content first.');
        return;
      }
    }

    try {
      setIsLoading(true);
      setError(null);
      const result = await previewInventoryImport(parsedRows);
      setPreviewResult(result);
      setStep(2);
    } catch (err: any) {
      setError(err.message || 'Validation preview failed');
    } finally {
      setIsLoading(false);
    }
  };

  const handleApply = async () => {
    if (!previewResult) return;
    if (importMode === 'replace' && !confirmReplace) {
      setError('Please explicitly check the confirmation box to replace existing physical stock.');
      return;
    }

    try {
      setIsLoading(true);
      setError(null);
      await applyInventoryImport(parsedRows, importMode);
      onSuccess();
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to apply import');
    } finally {
      setIsLoading(false);
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
      id="inventory-import-modal"
    >
      <div
        style={{
          background: '#0f172a',
          border: '1px solid rgba(255, 255, 255, 0.15)',
          borderRadius: '12px',
          width: '100%',
          maxWidth: '780px',
          maxHeight: '90vh',
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
              Import Inventory (CSV / Excel)
            </h3>
            <p style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
              Step {step} of 2: {step === 1 ? 'Upload & Dry-Run Validation' : 'Review & Apply Changes'}
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

        {/* Body */}
        <div style={{ padding: '1.5rem', overflowY: 'auto', flex: 1 }}>
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

          {step === 1 && (
            <div>
              <div
                style={{
                  border: '2px dashed rgba(255, 255, 255, 0.15)',
                  borderRadius: '8px',
                  padding: '1.5rem',
                  textAlign: 'center',
                  marginBottom: '1.25rem',
                  background: 'rgba(30, 41, 59, 0.3)',
                }}
              >
                <div style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>📄</div>
                <div style={{ fontWeight: 600, color: '#f8fafc', marginBottom: '0.25rem' }}>
                  Select CSV File to Upload
                </div>
                <div style={{ fontSize: '0.78rem', color: '#94a3b8', marginBottom: '1rem' }}>
                  Supported headers: <code>SKU</code>, <code>Quantity</code>, <code>Price</code> (or <code>Cost</code>)
                </div>
                <input
                  type="file"
                  accept=".csv,.txt"
                  onChange={handleFileUpload}
                  id="csv-file-input"
                  style={{ fontSize: '0.85rem', color: '#cbd5e1' }}
                />
              </div>

              <div>
                <label style={{ display: 'block', fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.4rem', fontWeight: 600 }}>
                  Or Paste CSV Data Below:
                </label>
                <textarea
                  rows={6}
                  value={fileText}
                  onChange={(e) => {
                    setFileText(e.target.value);
                    parseCsv(e.target.value);
                  }}
                  placeholder="SKU,Quantity,Price&#10;XG-501,150,45.00&#10;XG-502,300,120.00"
                  id="csv-text-input"
                  style={{
                    width: '100%',
                    background: 'rgba(15, 23, 42, 0.8)',
                    border: '1px solid rgba(255, 255, 255, 0.15)',
                    borderRadius: '6px',
                    color: '#f8fafc',
                    padding: '0.75rem',
                    fontSize: '0.82rem',
                    fontFamily: 'monospace',
                  }}
                />
              </div>

              {parsedRows.length > 0 && (
                <div style={{ marginTop: '0.75rem', fontSize: '0.8rem', color: '#34d399' }}>
                  ✓ {parsedRows.length} rows parsed and ready for dry-run validation.
                </div>
              )}
            </div>
          )}

          {step === 2 && previewResult && (
            <div>
              {/* Summary Stats Badges */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0.75rem', marginBottom: '1.25rem' }}>
                <div style={{ background: 'rgba(255, 255, 255, 0.05)', padding: '0.75rem', borderRadius: '6px', textAlign: 'center' }}>
                  <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>TOTAL ROWS</div>
                  <div style={{ fontSize: '1.3rem', fontWeight: 700, color: '#f8fafc' }}>{previewResult.total_rows}</div>
                </div>
                <div style={{ background: 'rgba(16, 185, 129, 0.1)', padding: '0.75rem', borderRadius: '6px', textAlign: 'center', border: '1px solid rgba(16, 185, 129, 0.3)' }}>
                  <div style={{ fontSize: '0.72rem', color: '#34d399' }}>VALID ROWS</div>
                  <div style={{ fontSize: '1.3rem', fontWeight: 700, color: '#34d399' }}>{previewResult.valid_rows}</div>
                </div>
                <div style={{ background: 'rgba(244, 63, 94, 0.1)', padding: '0.75rem', borderRadius: '6px', textAlign: 'center', border: '1px solid rgba(244, 63, 94, 0.3)' }}>
                  <div style={{ fontSize: '0.72rem', color: '#fb7185' }}>INVALID ROWS</div>
                  <div style={{ fontSize: '1.3rem', fontWeight: 700, color: '#fb7185' }}>{previewResult.invalid_rows}</div>
                </div>
                <div style={{ background: 'rgba(245, 158, 11, 0.1)', padding: '0.75rem', borderRadius: '6px', textAlign: 'center', border: '1px solid rgba(245, 158, 11, 0.3)' }}>
                  <div style={{ fontSize: '0.72rem', color: '#fbbf24' }}>DUPLICATE SKUS</div>
                  <div style={{ fontSize: '1.3rem', fontWeight: 700, color: '#fbbf24' }}>{previewResult.duplicate_skus_count}</div>
                </div>
              </div>

              {/* Mode Selection */}
              <div
                style={{
                  background: 'rgba(30, 41, 59, 0.6)',
                  border: '1px solid rgba(255, 255, 255, 0.1)',
                  borderRadius: '8px',
                  padding: '1rem',
                  marginBottom: '1.25rem',
                }}
              >
                <div style={{ fontWeight: 600, fontSize: '0.85rem', color: '#f8fafc', marginBottom: '0.5rem' }}>
                  Choose Application Mode:
                </div>
                <div style={{ display: 'flex', gap: '1.5rem', marginBottom: '0.75rem' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.85rem', color: '#f8fafc', cursor: 'pointer' }}>
                    <input
                      type="radio"
                      name="importMode"
                      value="add"
                      checked={importMode === 'add'}
                      onChange={() => setImportMode('add')}
                    />
                    <span><strong>Add mode:</strong> Add imported quantity to current stock</span>
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.85rem', color: '#f8fafc', cursor: 'pointer' }}>
                    <input
                      type="radio"
                      name="importMode"
                      value="replace"
                      checked={importMode === 'replace'}
                      onChange={() => setImportMode('replace')}
                    />
                    <span><strong>Replace mode:</strong> Overwrite stock with imported quantity</span>
                  </label>
                </div>

                {importMode === 'replace' && (
                  <div
                    style={{
                      background: 'rgba(244, 63, 94, 0.15)',
                      border: '1px solid rgba(244, 63, 94, 0.3)',
                      padding: '0.65rem 0.85rem',
                      borderRadius: '6px',
                      fontSize: '0.8rem',
                      color: '#fb7185',
                    }}
                  >
                    <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={confirmReplace}
                        onChange={(e) => setConfirmReplace(e.target.checked)}
                        id="confirm-replace-checkbox"
                      />
                      <span>
                        ⚠️ <strong>Confirmation Required:</strong> I understand that Replace mode will overwrite existing physical stock quantities for these SKUs.
                      </span>
                    </label>
                  </div>
                )}
              </div>

              {/* Rows Preview Table */}
              <div style={{ maxHeight: '240px', overflowY: 'auto', border: '1px solid rgba(255, 255, 255, 0.08)', borderRadius: '6px' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
                  <thead>
                    <tr style={{ background: 'rgba(15, 23, 42, 0.9)', color: '#94a3b8', textAlign: 'left' }}>
                      <th style={{ padding: '0.5rem 0.75rem' }}>#</th>
                      <th style={{ padding: '0.5rem 0.75rem' }}>SKU</th>
                      <th style={{ padding: '0.5rem 0.75rem' }}>Product Name</th>
                      <th style={{ padding: '0.5rem 0.75rem', textAlign: 'right' }}>Import Qty</th>
                      <th style={{ padding: '0.5rem 0.75rem', textAlign: 'right' }}>Import Cost</th>
                      <th style={{ padding: '0.5rem 0.75rem' }}>Validation</th>
                    </tr>
                  </thead>
                  <tbody>
                    {previewResult.rows.map((r) => {
                      const isValid = r.status === 'VALID';
                      return (
                        <tr
                          key={r.row_number}
                          style={{
                            borderBottom: '1px solid rgba(255, 255, 255, 0.04)',
                            background: isValid ? 'transparent' : 'rgba(244, 63, 94, 0.05)',
                          }}
                        >
                          <td style={{ padding: '0.5rem 0.75rem', color: '#64748b' }}>{r.row_number}</td>
                          <td style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: isValid ? '#f8fafc' : '#fb7185' }}>
                            {r.sku}
                          </td>
                          <td style={{ padding: '0.5rem 0.75rem', color: '#cbd5e1' }}>{r.product_name || '—'}</td>
                          <td style={{ padding: '0.5rem 0.75rem', textAlign: 'right', fontWeight: 600 }}>
                            {r.import_quantity !== null ? r.import_quantity : '—'}
                          </td>
                          <td style={{ padding: '0.5rem 0.75rem', textAlign: 'right', color: '#94a3b8' }}>
                            {r.imported_unit_cost !== null ? `₹${r.imported_unit_cost.toFixed(2)}` : '—'}
                          </td>
                          <td style={{ padding: '0.5rem 0.75rem' }}>
                            {isValid ? (
                              <span style={{ color: '#34d399', fontWeight: 600 }}>✓ Valid</span>
                            ) : (
                              <span style={{ color: '#fb7185' }}>{r.errors.join('; ')}</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '1rem 1.5rem',
            borderTop: '1px solid rgba(255, 255, 255, 0.1)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          {step === 2 ? (
            <button
              type="button"
              onClick={() => setStep(1)}
              style={{
                background: 'transparent',
                border: '1px solid rgba(255, 255, 255, 0.2)',
                color: '#94a3b8',
                padding: '0.5rem 1rem',
                borderRadius: '6px',
                fontSize: '0.85rem',
                cursor: 'pointer',
              }}
            >
              ← Back to File
            </button>
          ) : (
            <div />
          )}

          <div style={{ display: 'flex', gap: '0.75rem' }}>
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

            {step === 1 ? (
              <button
                type="button"
                onClick={handleRunPreview}
                disabled={isLoading}
                id="run-preview-btn"
                style={{
                  background: '#6366f1',
                  border: 'none',
                  color: '#ffffff',
                  padding: '0.55rem 1.25rem',
                  borderRadius: '6px',
                  fontSize: '0.85rem',
                  fontWeight: 600,
                  cursor: isLoading ? 'not-allowed' : 'pointer',
                }}
              >
                {isLoading ? 'Validating...' : 'Validate & Preview →'}
              </button>
            ) : (
              <button
                type="button"
                onClick={handleApply}
                disabled={isLoading || !previewResult || previewResult.valid_rows === 0}
                id="apply-import-btn"
                style={{
                  background: '#10b981',
                  border: 'none',
                  color: '#ffffff',
                  padding: '0.55rem 1.25rem',
                  borderRadius: '6px',
                  fontSize: '0.85rem',
                  fontWeight: 600,
                  cursor: isLoading || !previewResult || previewResult.valid_rows === 0 ? 'not-allowed' : 'pointer',
                }}
              >
                {isLoading ? 'Applying...' : `Apply ${previewResult?.valid_rows || 0} Valid Rows`}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
