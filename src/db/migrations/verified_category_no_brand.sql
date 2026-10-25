-- Verified No Brand + brand rejection blocks (apply manually; do NOT auto-run).
-- Safe apply (Postgres):
--   psql "$DATABASE_URL" -f src/db/migrations/verified_category_no_brand.sql
-- Or from any SQL client connected to the app DB.
-- Idempotent (IF NOT EXISTS). Does not mutate Daraz / seller data.

CREATE TABLE IF NOT EXISTS verified_category_no_brand (
    marketplace TEXT NOT NULL,
    primary_category_id TEXT NOT NULL,
    brand_name TEXT NOT NULL,
    brand_id TEXT NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (marketplace, primary_category_id)
);

CREATE TABLE IF NOT EXISTS category_brand_rejection_blocks (
    store_id TEXT NOT NULL,
    primary_category_id TEXT NOT NULL,
    marketplace TEXT NOT NULL DEFAULT 'pk',
    detail_code TEXT,
    message TEXT,
    blocked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cleared_at TIMESTAMPTZ,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (store_id, primary_category_id)
);

-- Optional durable create-attempt brand snapshot (nullable JSON).
ALTER TABLE product_create_attempts
    ADD COLUMN IF NOT EXISTS brand_resolution JSONB;
