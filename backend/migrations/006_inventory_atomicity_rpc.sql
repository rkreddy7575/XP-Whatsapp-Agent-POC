-- =============================================================================
-- Migration: 006_inventory_atomicity_rpc.sql
-- Project: Mudhra B2B Corporate Gifting Platform
-- Description: Enforces database-level atomicity for inventory adjustments and
--              transaction logging, with partial unique index for idempotent retries.
-- =============================================================================

-- 1. Partial Unique Index to enforce transaction reference uniqueness per SKU
--    Prevents concurrent or retried requests from creating duplicate transactions.
--    Allows multiple manual transactions where reference_id IS NULL.
CREATE UNIQUE INDEX IF NOT EXISTS uq_inv_tx_tenant_ref_sku 
ON inventory_transactions (tenant_id, reference_type, reference_id, sku) 
WHERE reference_id IS NOT NULL;

-- 2. Atomic Inventory Stock Adjustment Stored Function (RPC)
--    Performs row locking, idempotency check, stock calculation, inventory upsert,
--    and transaction audit logging inside a single ACID PostgreSQL transaction.
CREATE OR REPLACE FUNCTION adjust_inventory_stock_atomic(
    p_tenant_id VARCHAR,
    p_sku VARCHAR,
    p_action VARCHAR,
    p_quantity INTEGER DEFAULT NULL,
    p_reorder_level INTEGER DEFAULT NULL,
    p_unit_cost NUMERIC DEFAULT NULL,
    p_reason TEXT DEFAULT 'Stock adjustment',
    p_reference_type VARCHAR DEFAULT 'MANUAL',
    p_reference_id VARCHAR DEFAULT NULL,
    p_notes TEXT DEFAULT NULL,
    p_created_by VARCHAR DEFAULT 'owner'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_tenant VARCHAR := COALESCE(NULLIF(TRIM(p_tenant_id), ''), 'default');
    v_sku VARCHAR := UPPER(TRIM(p_sku));
    v_action VARCHAR := UPPER(TRIM(p_action));
    v_ref_type VARCHAR := COALESCE(NULLIF(TRIM(p_reference_type), ''), 'MANUAL');
    v_ref_id VARCHAR := NULLIF(TRIM(p_reference_id), '');
    v_now TIMESTAMPTZ := TIMEZONE('utc'::text, NOW());

    v_existing_tx_id UUID;
    v_curr_physical INTEGER;
    v_curr_reserved INTEGER := 0;
    v_curr_reorder INTEGER := 100;
    v_curr_cost NUMERIC;
    v_curr_status VARCHAR := 'UNKNOWN';
    v_curr_supplier_id VARCHAR;

    v_base_val INTEGER := 0;
    v_new_physical INTEGER;
    v_tx_type VARCHAR;
    v_qty_change INTEGER := 0;
    v_qty_int INTEGER;
    v_new_status VARCHAR;
    v_available INTEGER;
    v_eff_cost NUMERIC;
    v_tx_id UUID;
BEGIN
    -- 1. Idempotency Check: if reference_id provided, check if already recorded
    IF v_ref_id IS NOT NULL THEN
        SELECT id INTO v_existing_tx_id
        FROM inventory_transactions
        WHERE tenant_id = v_tenant
          AND reference_type = v_ref_type
          AND reference_id = v_ref_id
          AND UPPER(sku) = v_sku
        LIMIT 1;

        IF v_existing_tx_id IS NOT NULL THEN
            -- Already applied! Return existing inventory state without modifying stock
            SELECT 
                physical_quantity,
                reserved_quantity,
                reorder_level,
                unit_cost,
                status,
                supplier_id,
                last_updated
            INTO
                v_curr_physical,
                v_curr_reserved,
                v_curr_reorder,
                v_curr_cost,
                v_curr_status,
                v_curr_supplier_id,
                v_now
            FROM inventory
            WHERE tenant_id = v_tenant AND sku = v_sku;

            RETURN jsonb_build_object(
                'sku', v_sku,
                'tenant_id', v_tenant,
                'physical_quantity', v_curr_physical,
                'reserved_quantity', COALESCE(v_curr_reserved, 0),
                'reorder_level', COALESCE(v_curr_reorder, 100),
                'unit_cost', v_curr_cost,
                'status', COALESCE(v_curr_status, 'UNKNOWN'),
                'supplier_id', v_curr_supplier_id,
                'last_updated', v_now,
                'transaction_id', v_existing_tx_id,
                'already_applied', true
            );
        END IF;
    END IF;

    -- 2. Lock current inventory row (if exists) for atomic read-modify-write
    SELECT 
        physical_quantity,
        reserved_quantity,
        reorder_level,
        unit_cost,
        status,
        supplier_id
    INTO
        v_curr_physical,
        v_curr_reserved,
        v_curr_reorder,
        v_curr_cost,
        v_curr_status,
        v_curr_supplier_id
    FROM inventory
    WHERE tenant_id = v_tenant AND sku = v_sku
    FOR UPDATE;

    IF NOT FOUND THEN
        v_curr_physical := NULL;
        v_curr_reserved := 0;
        v_curr_reorder := COALESCE(p_reorder_level, 100);
        v_curr_cost := p_unit_cost;
        v_curr_status := 'UNKNOWN';
        v_curr_supplier_id := NULL;
    ELSE
        v_curr_reserved := COALESCE(v_curr_reserved, 0);
        v_curr_reorder := COALESCE(p_reorder_level, v_curr_reorder, 100);
        v_curr_cost := COALESCE(p_unit_cost, v_curr_cost);
    END IF;

    v_base_val := COALESCE(v_curr_physical, 0);

    -- 3. Calculate new physical stock and transaction details
    IF p_quantity IS NULL THEN
        IF v_action IN ('CORRECTION', 'SET') THEN
            v_new_physical := NULL;
            v_tx_type := 'STOCK_ADJUSTMENT';
            v_qty_change := -COALESCE(v_curr_physical, 0);
        ELSIF v_action IN ('RECEIVE', 'ADD') THEN
            v_new_physical := v_curr_physical;
            v_tx_type := 'STOCK_ADDED';
            v_qty_change := 0;
        ELSE
            v_new_physical := v_curr_physical;
            v_tx_type := 'STOCK_ADJUSTMENT';
            v_qty_change := 0;
        END IF;
    ELSE
        v_qty_int := p_quantity;
        IF v_action IN ('RECEIVE', 'ADD') THEN
            v_new_physical := v_base_val + GREATEST(0, v_qty_int);
            v_tx_type := CASE WHEN v_action = 'RECEIVE' THEN 'STOCK_RECEIVED' ELSE 'STOCK_ADDED' END;
            v_qty_change := GREATEST(0, v_qty_int);
        ELSIF v_action = 'REMOVE' THEN
            v_new_physical := GREATEST(0, v_base_val - GREATEST(0, v_qty_int));
            v_tx_type := 'STOCK_REMOVED';
            v_qty_change := -(v_base_val - v_new_physical);
        ELSIF v_action = 'DAMAGED' THEN
            v_new_physical := GREATEST(0, v_base_val - GREATEST(0, v_qty_int));
            v_tx_type := 'DAMAGED';
            v_qty_change := -(v_base_val - v_new_physical);
        ELSIF v_action IN ('CORRECTION', 'SET') THEN
            v_new_physical := GREATEST(0, v_qty_int);
            v_tx_type := 'STOCK_ADJUSTMENT';
            v_qty_change := v_new_physical - v_base_val;
        ELSE
            v_new_physical := GREATEST(0, v_qty_int);
            v_tx_type := 'STOCK_ADJUSTMENT';
            v_qty_change := v_new_physical - v_base_val;
        END IF;
    END IF;

    -- 4. Compute status
    IF v_curr_status IN ('COMING_SOON', 'SUPPLIER_CONFIRMATION_REQUIRED') THEN
        v_new_status := v_curr_status;
    ELSIF v_new_physical IS NULL THEN
        v_new_status := 'UNKNOWN';
    ELSE
        v_available := GREATEST(0, v_new_physical - v_curr_reserved);
        IF v_available = 0 THEN
            v_new_status := 'OUT_OF_STOCK';
        ELSIF v_available <= v_curr_reorder THEN
            v_new_status := 'LOW_STOCK';
        ELSE
            v_new_status := 'IN_STOCK';
        END IF;
    END IF;

    v_eff_cost := COALESCE(p_unit_cost, v_curr_cost);

    -- 5. Atomic Write 1: Upsert inventory
    INSERT INTO inventory (
        tenant_id, sku, physical_quantity, reserved_quantity,
        reorder_level, unit_cost, status, supplier_id, last_updated
    ) VALUES (
        v_tenant, v_sku, v_new_physical, v_curr_reserved,
        v_curr_reorder, v_eff_cost, v_new_status, v_curr_supplier_id, v_now
    )
    ON CONFLICT (tenant_id, sku) DO UPDATE SET
        physical_quantity = EXCLUDED.physical_quantity,
        reserved_quantity = EXCLUDED.reserved_quantity,
        reorder_level = EXCLUDED.reorder_level,
        unit_cost = COALESCE(EXCLUDED.unit_cost, inventory.unit_cost),
        status = EXCLUDED.status,
        supplier_id = COALESCE(EXCLUDED.supplier_id, inventory.supplier_id),
        last_updated = EXCLUDED.last_updated;

    -- 6. Atomic Write 2: Insert transaction audit
    INSERT INTO inventory_transactions (
        tenant_id, sku, transaction_type, quantity_change,
        quantity_before, quantity_after, reason, reference_type,
        reference_id, notes, created_by, created_at
    ) VALUES (
        v_tenant, v_sku, v_tx_type, v_qty_change,
        v_curr_physical, v_new_physical, COALESCE(p_reason, 'Stock adjustment'),
        v_ref_type, v_ref_id, p_notes, COALESCE(p_created_by, 'owner'), v_now
    ) RETURNING id INTO v_tx_id;

    -- 7. Return updated inventory item state
    RETURN jsonb_build_object(
        'sku', v_sku,
        'tenant_id', v_tenant,
        'physical_quantity', v_new_physical,
        'reserved_quantity', v_curr_reserved,
        'reorder_level', v_curr_reorder,
        'unit_cost', v_eff_cost,
        'status', v_new_status,
        'supplier_id', v_curr_supplier_id,
        'last_updated', v_now,
        'transaction_id', v_tx_id,
        'already_applied', false
    );
END;
$$;

-- Security: Restrict execution strictly to service_role (backend server only)
-- Prevents unauthorized direct client-side RPC execution via anon/authenticated Supabase JWTs
REVOKE ALL ON FUNCTION adjust_inventory_stock_atomic(VARCHAR, VARCHAR, VARCHAR, INTEGER, INTEGER, NUMERIC, TEXT, VARCHAR, VARCHAR, TEXT, VARCHAR) FROM PUBLIC;
REVOKE ALL ON FUNCTION adjust_inventory_stock_atomic(VARCHAR, VARCHAR, VARCHAR, INTEGER, INTEGER, NUMERIC, TEXT, VARCHAR, VARCHAR, TEXT, VARCHAR) FROM anon;
REVOKE ALL ON FUNCTION adjust_inventory_stock_atomic(VARCHAR, VARCHAR, VARCHAR, INTEGER, INTEGER, NUMERIC, TEXT, VARCHAR, VARCHAR, TEXT, VARCHAR) FROM authenticated;
GRANT EXECUTE ON FUNCTION adjust_inventory_stock_atomic(VARCHAR, VARCHAR, VARCHAR, INTEGER, INTEGER, NUMERIC, TEXT, VARCHAR, VARCHAR, TEXT, VARCHAR) TO service_role;
