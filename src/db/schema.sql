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
