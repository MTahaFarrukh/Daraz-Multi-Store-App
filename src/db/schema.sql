-- Phase 1A: multi-tenant SaaS schema (Supabase / Postgres)
-- Idempotent: safe to run multiple times.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS workspaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'staff')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_members_user
    ON workspace_members (user_id);

CREATE TABLE IF NOT EXISTS daraz_stores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    store_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    store_name TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    seller_id TEXT NOT NULL DEFAULT '',
    daraz_user_id TEXT,
    country TEXT NOT NULL DEFAULT '',
    account_platform TEXT NOT NULL DEFAULT '',
    country_user_info JSONB NOT NULL DEFAULT '[]'::jsonb,
    access_token_enc TEXT NOT NULL,
    refresh_token_enc TEXT NOT NULL,
    expires_in INTEGER,
    refresh_expires_in INTEGER,
    access_token_expires_at TIMESTAMPTZ,
    refresh_token_expires_at TIMESTAMPTZ,
    authorized_at TIMESTAMPTZ,
    request_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, store_id)
);

CREATE INDEX IF NOT EXISTS idx_daraz_stores_workspace
    ON daraz_stores (workspace_id);

CREATE INDEX IF NOT EXISTS idx_daraz_stores_account
    ON daraz_stores (workspace_id, account);

CREATE TABLE IF NOT EXISTS store_groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS store_group_members (
    group_id UUID NOT NULL REFERENCES store_groups(id) ON DELETE CASCADE,
    store_id TEXT NOT NULL,
    PRIMARY KEY (group_id, store_id)
);

CREATE TABLE IF NOT EXISTS print_jobs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    error TEXT,
    result JSONB,
    output_path TEXT,
    started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_print_jobs_workspace
    ON print_jobs (workspace_id, updated_at DESC);

-- Phase 2.5B: monthly store performance aggregates (workspace-scoped; FK to daraz_stores.id)
CREATE TABLE IF NOT EXISTS store_performance_monthly (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES daraz_stores(id) ON DELETE CASCADE,
    year INT NOT NULL CHECK (year >= 2000 AND year <= 2100),
    month INT NOT NULL CHECK (month >= 1 AND month <= 12),
    orders_count INT NOT NULL DEFAULT 0,
    gross_sales NUMERIC(18, 2),
    currency TEXT,
    previous_orders_count INT,
    orders_growth_pct NUMERIC(12, 4),
    previous_gross_sales NUMERIC(18, 2),
    gross_sales_growth_pct NUMERIC(12, 4),
    source TEXT NOT NULL DEFAULT 'orders_api',
    sync_status TEXT NOT NULL DEFAULT 'ok',
    sync_error TEXT,
    orders_synced_at TIMESTAMPTZ,
    gross_sales_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, year, month)
);

CREATE INDEX IF NOT EXISTS idx_store_perf_monthly_workspace_period
    ON store_performance_monthly (workspace_id, year DESC, month DESC);

-- Phase 3: unified local orders + label print history (order-scoped)
CREATE TABLE IF NOT EXISTS daraz_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES daraz_stores(id) ON DELETE CASCADE,
    daraz_order_id TEXT NOT NULL,
    order_number TEXT,
    status_raw TEXT,
    status_group TEXT,
    statuses JSONB,
    created_at_daraz TIMESTAMPTZ,
    updated_at_daraz TIMESTAMPTZ,
    price NUMERIC,
    currency TEXT,
    items_count INT,
    customer_first_name TEXT,
    customer_last_name TEXT,
    address_shipping JSONB,
    address_billing JSONB,
    payment_method TEXT,
    shipping_fee NUMERIC,
    warehouse_code TEXT,
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, daraz_order_id)
);

CREATE INDEX IF NOT EXISTS idx_daraz_orders_workspace
    ON daraz_orders (workspace_id);

CREATE INDEX IF NOT EXISTS idx_daraz_orders_workspace_status_group
    ON daraz_orders (workspace_id, status_group);

CREATE INDEX IF NOT EXISTS idx_daraz_orders_workspace_created_daraz
    ON daraz_orders (workspace_id, created_at_daraz DESC);

CREATE INDEX IF NOT EXISTS idx_daraz_orders_order_number
    ON daraz_orders (order_number);

CREATE TABLE IF NOT EXISTS daraz_order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES daraz_stores(id) ON DELETE CASCADE,
    order_id UUID NOT NULL REFERENCES daraz_orders(id) ON DELETE CASCADE,
    daraz_order_item_id TEXT NOT NULL,
    daraz_order_id TEXT,
    status_raw TEXT,
    package_id TEXT,
    name TEXT,
    sku TEXT,
    sku_id TEXT,
    product_id TEXT,
    quantity INT NOT NULL DEFAULT 1,
    item_price NUMERIC,
    paid_price NUMERIC,
    currency TEXT,
    tracking_code TEXT,
    shipment_provider TEXT,
    shipping_type TEXT,
    warehouse_code TEXT,
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, daraz_order_item_id)
);

CREATE INDEX IF NOT EXISTS idx_daraz_order_items_order
    ON daraz_order_items (order_id);

CREATE INDEX IF NOT EXISTS idx_daraz_order_items_workspace
    ON daraz_order_items (workspace_id);

CREATE TABLE IF NOT EXISTS order_label_prints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES daraz_stores(id) ON DELETE CASCADE,
    order_id UUID NOT NULL REFERENCES daraz_orders(id) ON DELETE CASCADE,
    daraz_order_id TEXT NOT NULL,
    package_id TEXT,
    order_item_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    print_job_id UUID REFERENCES print_jobs(id) ON DELETE SET NULL,
    printed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    printed_by_user_id UUID,
    is_reprint BOOLEAN NOT NULL DEFAULT false,
    fetch_source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_order_label_prints_order
    ON order_label_prints (order_id);

CREATE INDEX IF NOT EXISTS idx_order_label_prints_workspace_printed
    ON order_label_prints (workspace_id, printed_at DESC);

CREATE INDEX IF NOT EXISTS idx_order_label_prints_store_daraz_order
    ON order_label_prints (store_id, daraz_order_id);

-- Optional summary columns on existing print_jobs (idempotent)
ALTER TABLE print_jobs ADD COLUMN IF NOT EXISTS new_labels_count INT;
ALTER TABLE print_jobs ADD COLUMN IF NOT EXISTS reprint_count INT;
ALTER TABLE print_jobs ADD COLUMN IF NOT EXISTS failed_count INT;
ALTER TABLE print_jobs ADD COLUMN IF NOT EXISTS store_ids JSONB;

-- Phase 4B: local product warehouse (products + variants + per-workspace defaults)
CREATE TABLE IF NOT EXISTS daraz_products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES daraz_stores(id) ON DELETE CASCADE,
    daraz_item_id TEXT NOT NULL,
    title TEXT,
    title_en TEXT,
    primary_category_id BIGINT,
    primary_category_name TEXT,
    brand TEXT,
    description TEXT,
    description_en TEXT,
    short_description TEXT,
    short_description_en TEXT,
    package_content TEXT,
    status_raw TEXT,
    product_url TEXT,
    attributes_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    variation_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- images_json entries: [{url, position, kind}]
    images_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    market_images_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- opaque platform video id; informational only (cannot be re-uploaded via API)
    video_ref TEXT,
    raw_json JSONB,
    -- Phase 4D: catalog list vs detail hydration
    catalog_seen_at TIMESTAMPTZ,
    detail_synced_at TIMESTAMPTZ,
    detail_complete BOOLEAN NOT NULL DEFAULT FALSE,
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, daraz_item_id)
);

-- Idempotent upgrades for existing databases
ALTER TABLE daraz_products ADD COLUMN IF NOT EXISTS catalog_seen_at TIMESTAMPTZ;
ALTER TABLE daraz_products ADD COLUMN IF NOT EXISTS detail_synced_at TIMESTAMPTZ;
ALTER TABLE daraz_products ADD COLUMN IF NOT EXISTS detail_complete BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_daraz_products_workspace
    ON daraz_products (workspace_id);

CREATE INDEX IF NOT EXISTS idx_daraz_products_workspace_status
    ON daraz_products (workspace_id, status_raw);

CREATE INDEX IF NOT EXISTS idx_daraz_products_workspace_category
    ON daraz_products (workspace_id, primary_category_id);

CREATE INDEX IF NOT EXISTS idx_daraz_products_workspace_title_lower
    ON daraz_products (workspace_id, lower(title));

CREATE TABLE IF NOT EXISTS daraz_product_variants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES daraz_stores(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES daraz_products(id) ON DELETE CASCADE,
    daraz_sku_id TEXT NOT NULL,
    seller_sku TEXT,
    shop_sku TEXT,
    sale_props_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    price NUMERIC(18, 2),
    special_price NUMERIC(18, 2),
    quantity INT,
    package_weight NUMERIC(18, 4),
    package_length NUMERIC(18, 4),
    package_width NUMERIC(18, 4),
    package_height NUMERIC(18, 4),
    images_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    status_raw TEXT,
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, daraz_sku_id)
);

CREATE INDEX IF NOT EXISTS idx_daraz_product_variants_product
    ON daraz_product_variants (product_id);

CREATE INDEX IF NOT EXISTS idx_daraz_product_variants_workspace
    ON daraz_product_variants (workspace_id);

CREATE INDEX IF NOT EXISTS idx_daraz_product_variants_store_seller_sku
    ON daraz_product_variants (store_id, seller_sku);

CREATE TABLE IF NOT EXISTS workspace_product_defaults (
    workspace_id UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    default_package_weight NUMERIC(18, 4),
    default_package_length NUMERIC(18, 4),
    default_package_width NUMERIC(18, 4),
    default_package_height NUMERIC(18, 4),
    default_initial_quantity INT NOT NULL DEFAULT 1,
    sku_prefix TEXT NOT NULL DEFAULT 'MTF-',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
