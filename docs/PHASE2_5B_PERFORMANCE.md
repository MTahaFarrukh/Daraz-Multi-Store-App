# Phase 2.5B — Store Performance Foundation

## Marketplace timezone

Daraz Open Platform order timestamps observed in Phase 2.5A used **+0800**
(Lazada-style), not Pakistan local (+05:00). Monthly aggregation therefore uses:

- Timezone: `UTC+08:00`
- Module: `src/performance_time.py`
- Window: inclusive start `YYYY-MM-01T00:00:00+08:00`, exclusive end next month

Timestamps stored in Postgres use `TIMESTAMPTZ` (UTC-normalized).

## Orders metric

Aligned with Seller Center **Data Insights** (excludes cancelled):

```
orders_count = countTotal(status=all) - countTotal(status=canceled)
```

Two cheap `/orders/get` calls per store/month (`limit=1`).

Gross Sales sums `order.price` while paginating `status=all`, **skipping**
canceled order rows so it stays consistent with the Orders metric.

**ENABLED**

Evidence (Phase 2.5A re-validation): `/orders/get` with `limit=100` and
offsets `0 / 100 / 200` returned distinct pages with **0 order_id overlap**.
Gross Sales = sum of order `price` across pages (not Revenue / not Finance).

If overlap is detected at sync time, that store's `gross_sales` is set NULL
(`pagination_overlap_detected`) rather than summing incomplete data.

## Sync model (v1)

- On-demand: `POST /api/store-performance/sync`
- No Celery/Redis workers
- Partial failure: one store error does not wipe successful stores
- Recommended next step: lightweight cron hitting sync for current month
  every 15–30 minutes on Render if/when a scheduler is available

## Community readiness (not implemented)

Future tables (proposal only — **not created** in this phase):

```sql
-- communities (id, name, ...)
-- community_members (community_id, user_id, role)  -- NOT workspace_members
-- community_stores (community_id, store_id FK → daraz_stores.id, opted_in_at)
```

Performance rows stay keyed by `daraz_stores.id` + year/month.
Communities **reference** stores; they do **not** duplicate
`store_performance_monthly` rows.

Community membership ≠ workspace membership. Leaderboard endpoints later must
return only `leaderboard_safe_view` fields.

## Schema approach

Idempotent `src/db/schema.sql` applied via `ensure_saas_schema()` (existing
convention). Table: `store_performance_monthly`.
