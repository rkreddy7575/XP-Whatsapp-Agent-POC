-- =============================================================================
-- Migration: 005_inventory.sql
-- Project: Mudhra B2B Corporate Gifting Platform
-- Description: Durable Supabase PostgreSQL store for inventory management,
--              stock adjustments, reorder thresholds, and transaction history.
-- Multi-Tenant: Logical uniqueness enforced on (tenant_id, sku).
-- =============================================================================

CREATE TABLE IF NOT EXISTS inventory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    sku VARCHAR(100) NOT NULL,
    physical_quantity INTEGER, -- Nullable: NULL indicates UNKNOWN stock
    reserved_quantity INTEGER NOT NULL DEFAULT 0,
    reorder_level INTEGER NOT NULL DEFAULT 100,
    unit_cost NUMERIC(12, 2),
    status VARCHAR(50) NOT NULL DEFAULT 'UNKNOWN',
    supplier_id VARCHAR(100),
    last_updated TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    CONSTRAINT uq_inventory_tenant_sku UNIQUE (tenant_id, sku)
);

CREATE TABLE IF NOT EXISTS inventory_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    sku VARCHAR(100) NOT NULL,
    transaction_type VARCHAR(50) NOT NULL,
    quantity_change INTEGER NOT NULL,
    quantity_before INTEGER,
    quantity_after INTEGER,
    reason TEXT,
    reference_type VARCHAR(50),
    reference_id VARCHAR(100),
    notes TEXT,
    created_by VARCHAR(100) NOT NULL DEFAULT 'owner',
    created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW())
);

-- Performance indices for tenant-scoped lookups and aggregations
CREATE INDEX IF NOT EXISTS idx_inventory_tenant_sku ON inventory (tenant_id, sku);
CREATE INDEX IF NOT EXISTS idx_inventory_tenant_status ON inventory (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_inventory_tenant_updated ON inventory (tenant_id, last_updated DESC);

CREATE INDEX IF NOT EXISTS idx_inv_tx_tenant_sku ON inventory_transactions (tenant_id, sku);
CREATE INDEX IF NOT EXISTS idx_inv_tx_tenant_created ON inventory_transactions (tenant_id, created_at DESC);

COMMENT ON TABLE inventory IS 'Durable store of stock levels and reorder thresholds per product SKU.';
COMMENT ON TABLE inventory_transactions IS 'Immutable audit log of all stock movements, manual adjustments, and imports.';

-- Enable Row Level Security (RLS)
ALTER TABLE inventory ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_transactions ENABLE ROW LEVEL SECURITY;

-- Idempotent RLS Policies for service_role
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'inventory' 
        AND policyname = 'Allow service role full access to inventory'
    ) THEN
        CREATE POLICY "Allow service role full access to inventory"
        ON inventory
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'inventory_transactions' 
        AND policyname = 'Allow service role full access to inventory_transactions'
    ) THEN
        CREATE POLICY "Allow service role full access to inventory_transactions"
        ON inventory_transactions
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
    END IF;
END
$$;
