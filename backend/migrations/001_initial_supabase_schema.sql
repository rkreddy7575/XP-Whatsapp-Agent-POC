-- =============================================================================
-- Migration: 001_initial_supabase_schema.sql
-- Project: Mudhra B2B Corporate Gifting Platform
-- Description: Core schema replacing SQLite persistence with Supabase PostgreSQL.
-- Multi-Tenant Ready: Includes tenant_id on all entities (defaults to 'default').
-- Entities:
--   1. products
--   2. pricing_rules
--   3. customers
--   4. conversations
--   5. messages
--   6. enquiries
--   7. quotes
--   8. orders
--   9. order_items
--   10. order_status_history
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Function for automatic updated_at timestamp management
CREATE OR REPLACE FUNCTION trigger_set_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = TIMEZONE('utc'::text, NOW());
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- -----------------------------------------------------------------------------
-- 1. PRODUCTS
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    sku VARCHAR(100) NOT NULL,
    normalized_sku VARCHAR(100) NOT NULL,
    name VARCHAR(255),
    category VARCHAR(100) NOT NULL,
    subcategory VARCHAR(100),
    description TEXT,
    components JSONB DEFAULT '[]'::jsonb,
    colors JSONB DEFAULT '[]'::jsonb,
    moq INTEGER,
    image_url TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    source_file VARCHAR(255),
    source_sheet VARCHAR(100),
    source_row INTEGER,
    source_price VARCHAR(100),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    CONSTRAINT uq_products_tenant_sku UNIQUE (tenant_id, sku)
);

CREATE INDEX IF NOT EXISTS idx_products_tenant_norm_sku ON products (tenant_id, normalized_sku);
CREATE INDEX IF NOT EXISTS idx_products_tenant_category ON products (tenant_id, category);
CREATE INDEX IF NOT EXISTS idx_products_tenant_status ON products (tenant_id, status);

CREATE OR REPLACE TRIGGER set_products_timestamp
BEFORE UPDATE ON products
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- -----------------------------------------------------------------------------
-- 2. PRICING_RULES
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pricing_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    sku VARCHAR(100) NOT NULL,
    normalized_sku VARCHAR(100) NOT NULL,
    category VARCHAR(100),
    quantity_from INTEGER NOT NULL DEFAULT 1,
    quantity_to INTEGER, -- NULL indicates open-ended bracket (e.g. 500+)
    unit_price_excl_gst NUMERIC(12, 2) NOT NULL,
    gst_percentage NUMERIC(5, 2) NOT NULL DEFAULT 18.00,
    source_file VARCHAR(255),
    source_sheet VARCHAR(100),
    source_row INTEGER,
    source_category VARCHAR(100),
    pricing_version VARCHAR(50) NOT NULL DEFAULT '2025',
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    effective_from TIMESTAMPTZ,
    effective_to TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    CONSTRAINT uq_pricing_rules_bracket UNIQUE NULLS NOT DISTINCT (tenant_id, normalized_sku, quantity_from, quantity_to, pricing_version)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pricing_rules_bracket_idx 
ON pricing_rules (tenant_id, normalized_sku, quantity_from, COALESCE(quantity_to, -1), pricing_version);

CREATE INDEX IF NOT EXISTS idx_pricing_rules_lookup 
ON pricing_rules (tenant_id, normalized_sku, quantity_from);

CREATE INDEX IF NOT EXISTS idx_pricing_rules_category 
ON pricing_rules (tenant_id, category);

CREATE OR REPLACE TRIGGER set_pricing_rules_timestamp
BEFORE UPDATE ON pricing_rules
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- -----------------------------------------------------------------------------
-- 3. CUSTOMERS
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    phone VARCHAR(30) NOT NULL,
    name VARCHAR(255),
    email VARCHAR(255),
    company_name VARCHAR(255),
    gstin VARCHAR(50),
    billing_address TEXT,
    shipping_address TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    CONSTRAINT uq_customers_tenant_phone UNIQUE (tenant_id, phone)
);

CREATE INDEX IF NOT EXISTS idx_customers_tenant_phone ON customers (tenant_id, phone);

CREATE OR REPLACE TRIGGER set_customers_timestamp
BEFORE UPDATE ON customers
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- -----------------------------------------------------------------------------
-- 4. CONVERSATIONS
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    conversation_id VARCHAR(100) NOT NULL,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    customer_phone VARCHAR(30) NOT NULL,
    channel VARCHAR(50) NOT NULL DEFAULT 'WHATSAPP',
    last_intent VARCHAR(100),
    current_product_candidates JSONB DEFAULT '[]'::jsonb,
    selected_sku VARCHAR(100),
    selected_quantity INTEGER,
    pending_quote JSONB,
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    CONSTRAINT uq_conversations_tenant_conv_id UNIQUE (tenant_id, conversation_id),
    CONSTRAINT uq_conversations_tenant_phone UNIQUE (tenant_id, customer_phone)
);

CREATE INDEX IF NOT EXISTS idx_conversations_tenant_phone ON conversations (tenant_id, customer_phone);
CREATE INDEX IF NOT EXISTS idx_conversations_tenant_conv_id ON conversations (tenant_id, conversation_id);

CREATE OR REPLACE TRIGGER set_conversations_timestamp
BEFORE UPDATE ON conversations
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- -----------------------------------------------------------------------------
-- 5. MESSAGES
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    conversation_id VARCHAR(100) NOT NULL,
    direction VARCHAR(20) NOT NULL, -- INBOUND or OUTBOUND
    message_text TEXT NOT NULL,
    channel_message_id VARCHAR(255),
    sender_id VARCHAR(100),
    payload JSONB DEFAULT '{}'::jsonb,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW())
);

CREATE INDEX IF NOT EXISTS idx_messages_conv_timestamp ON messages (tenant_id, conversation_id, timestamp);

-- -----------------------------------------------------------------------------
-- 6. ENQUIRIES
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS enquiries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    enquiry_number VARCHAR(100) NOT NULL,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    customer_phone VARCHAR(30) NOT NULL,
    customer_name VARCHAR(255),
    conversation_id VARCHAR(100),
    status VARCHAR(50) NOT NULL DEFAULT 'NEW', -- NEW, QUOTED, CONVERTED, DROPPED
    category VARCHAR(100),
    sku VARCHAR(100),
    quantity INTEGER,
    customization_details TEXT,
    budget_per_unit NUMERIC(12, 2),
    target_date DATE,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    CONSTRAINT uq_enquiries_tenant_number UNIQUE (tenant_id, enquiry_number)
);

CREATE INDEX IF NOT EXISTS idx_enquiries_tenant_phone ON enquiries (tenant_id, customer_phone);
CREATE INDEX IF NOT EXISTS idx_enquiries_tenant_status ON enquiries (tenant_id, status);

CREATE OR REPLACE TRIGGER set_enquiries_timestamp
BEFORE UPDATE ON enquiries
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- -----------------------------------------------------------------------------
-- 7. QUOTES
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quotes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    quote_number VARCHAR(100) NOT NULL,
    enquiry_id UUID REFERENCES enquiries(id) ON DELETE SET NULL,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    customer_phone VARCHAR(30) NOT NULL,
    sku VARCHAR(100) NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price_excl_gst NUMERIC(12, 2) NOT NULL,
    gst_percentage NUMERIC(5, 2) NOT NULL,
    unit_gst NUMERIC(12, 2) NOT NULL,
    unit_price_incl_gst NUMERIC(12, 2) NOT NULL,
    total_price_excl_gst NUMERIC(12, 2) NOT NULL,
    total_gst NUMERIC(12, 2) NOT NULL,
    total_price_incl_gst NUMERIC(12, 2) NOT NULL,
    pricing_source VARCHAR(255),
    pricing_version VARCHAR(50) NOT NULL DEFAULT '2025',
    status VARCHAR(50) NOT NULL DEFAULT 'ISSUED', -- ISSUED, ACCEPTED, REJECTED, EXPIRED
    valid_until TIMESTAMPTZ,
    quote_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    CONSTRAINT uq_quotes_tenant_number UNIQUE (tenant_id, quote_number)
);

CREATE INDEX IF NOT EXISTS idx_quotes_tenant_phone ON quotes (tenant_id, customer_phone);
CREATE INDEX IF NOT EXISTS idx_quotes_tenant_sku ON quotes (tenant_id, sku);
CREATE INDEX IF NOT EXISTS idx_quotes_tenant_status ON quotes (tenant_id, status);

CREATE OR REPLACE TRIGGER set_quotes_timestamp
BEFORE UPDATE ON quotes
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- -----------------------------------------------------------------------------
-- 8. ORDERS
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    order_id VARCHAR(100) NOT NULL,
    quote_id UUID REFERENCES quotes(id) ON DELETE SET NULL,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    customer_phone VARCHAR(30) NOT NULL,
    customer_name VARCHAR(255),
    status VARCHAR(50) NOT NULL DEFAULT 'CONFIRMED',
    subtotal NUMERIC(12, 2) NOT NULL,
    gst_amount NUMERIC(12, 2) NOT NULL,
    grand_total NUMERIC(12, 2) NOT NULL,
    currency VARCHAR(10) NOT NULL DEFAULT 'INR',
    pricing_version VARCHAR(50) NOT NULL DEFAULT '2025',
    inventory_status VARCHAR(100) NOT NULL DEFAULT 'availability_confirmation_required',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW()),
    CONSTRAINT uq_orders_tenant_order_id UNIQUE (tenant_id, order_id)
);

CREATE INDEX IF NOT EXISTS idx_orders_tenant_order_id ON orders (tenant_id, order_id);
CREATE INDEX IF NOT EXISTS idx_orders_tenant_phone ON orders (tenant_id, customer_phone);
CREATE INDEX IF NOT EXISTS idx_orders_tenant_status ON orders (tenant_id, status);

CREATE OR REPLACE TRIGGER set_orders_timestamp
BEFORE UPDATE ON orders
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- -----------------------------------------------------------------------------
-- 9. ORDER_ITEMS
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_items (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    order_id VARCHAR(100) NOT NULL,
    sku VARCHAR(100) NOT NULL,
    product_name VARCHAR(255),
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(12, 2) NOT NULL,
    gst_rate NUMERIC(5, 2) NOT NULL,
    gst_amount NUMERIC(12, 2) NOT NULL,
    line_total NUMERIC(12, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW())
);

CREATE INDEX IF NOT EXISTS idx_order_items_tenant_order_id ON order_items (tenant_id, order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_tenant_sku ON order_items (tenant_id, sku);

-- -----------------------------------------------------------------------------
-- 10. ORDER_STATUS_HISTORY
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_status_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    order_id VARCHAR(100) NOT NULL,
    previous_status VARCHAR(50),
    new_status VARCHAR(50) NOT NULL,
    changed_by VARCHAR(100) DEFAULT 'system',
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT TIMEZONE('utc'::text, NOW())
);

CREATE INDEX IF NOT EXISTS idx_order_status_history_order ON order_status_history (tenant_id, order_id, created_at);
