# Phase 3 — Unified Orders (backend)

Local order cache + print-safety for multi-store label printing.

## Identity

| Concept | Key |
|--------|-----|
| Print target | `(store_uuid, daraz_order_id)` — **order-scoped** |
| Label document | **One PDF/HTML document per order** (eligible `item_ids` bundled) |
| Fetch path | `PrintAWB(package_id)` when present; else `GetDocument(item_ids)` |

`order_label_prints` stores `package_id` + `order_item_ids` on each print event.

## Status groups

`src/order_status.py` maps Daraz `status` / `statuses` (case-insensitive) →

`pending` · `ready_to_ship` · `shipped` · `delivered` · `canceled` · `returned` · `other`

## Sync windows

`src/order_sync.py`:

- Default incremental: `update_after = now − 7d`
- Broader / initial: `created_after = now − 30d` (or `days` on `POST /api/orders/sync`)
- Prefer `update_after` when provided
- Pagination: `PAGE_SIZE=100`, `MAX_OFFSET=5000`, overlap detection like performance sync
- **No finance APIs**

## API map

| Method | Path | Behavior |
|--------|------|----------|
| GET | `/api/orders` | **Local DB** list. Omit stores = all workspace stores. `stores=` empty → **400**. |
| GET | `/api/orders/live` | Live Daraz fetch (Shipping / legacy). Empty stores ≠ all. |
| GET | `/api/orders/status-counts` | Local counts by `status_group` |
| GET | `/api/orders/{order_id}` | Detail + items + print history (no tokens) |
| POST | `/api/orders/sync` | Pull Daraz → upsert local |
| POST | `/api/print-labels/validate` | `{order_ids}` → new / printed / not_eligible |
| POST | `/api/print-labels/orders` | Async job; `allow_reprint` |
| POST | `/api/print-labels` | Legacy live print; `allow_reprint=false` skips already-printed |

## Print eligibility

An order is printable if `status_group == ready_to_ship` **or** any item is label-eligible.
Without `allow_reprint`, already-printed orders are excluded. Failed PDF generation does **not** insert `order_label_prints`.

## Schema

Idempotent tables in `src/db/schema.sql`: `daraz_orders`, `daraz_order_items`, `order_label_prints`.
Existing `print_jobs` / `store_performance_monthly` are preserved (optional summary columns via `ADD COLUMN IF NOT EXISTS`).
