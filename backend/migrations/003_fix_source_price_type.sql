-- =============================================================================
-- Migration: 003_fix_source_price_type.sql
-- Description: 
--   1. Alters products.source_price from NUMERIC(12, 2) to VARCHAR(100)
--      to accommodate raw vendor catalogue price strings (e.g. "150/150/165", "640/800").
--   2. Adds unique constraint on pricing_rules columns to support PostgREST
--      ON CONFLICT (tenant_id, normalized_sku, quantity_from, quantity_to, pricing_version) DO UPDATE.
-- Note: Deterministic pricing logic remains strictly numeric in pricing_rules.
-- =============================================================================

-- 1. Fix source_price column type
ALTER TABLE products ALTER COLUMN source_price TYPE VARCHAR(100) USING source_price::varchar;

-- 2. Add column-level unique constraint for PostgREST upsert support
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_pricing_rules_bracket'
    ) THEN
        ALTER TABLE pricing_rules 
        ADD CONSTRAINT uq_pricing_rules_bracket 
        UNIQUE NULLS NOT DISTINCT (tenant_id, normalized_sku, quantity_from, quantity_to, pricing_version);
    END IF;
END $$;
