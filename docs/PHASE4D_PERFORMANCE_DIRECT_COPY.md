# Phase 4D — Core Performance + Direct Product Copy + Public Link Import

Status: implemented locally. Create Copy remains gated (Phase 4C supervised A→B create not completed — single connected store).

## Shipping (4D.1 hotfix)

- `POST /api/shipping/rts`: bounded concurrency (`STORE_CONCURRENCY=3`), live `/orders/get` only, header upsert + **batched** print-history reconcile. Returns `timings_ms` per store, per-page `request_timings`, and wall-clock total (**not** sum of store durations).
- Date strategy: primary `update_after` (`RTS_UPDATE_AFTER_DAYS=90`) + `status=ready_to_ship`; if empty, expand once with `created_after` (`RTS_CREATED_AFTER_DAYS=180`). Never “today only”.
- Pagination: `PAGE_SIZE=50`; stop when `countTotal<=limit` or `returned<limit` (24 RTS = one page).
- Print path: `hydrate_missing_order_items` before validation; every selected order gets an explicit outcome; print events only on SUCCESS after merge; UI shows `N/M` + **Retry Failed**.
- Label fetch: **bulk GetDocument preferred** (`PRINT_PREFER_BULK_GETDOCUMENT` default on) — one `get_shipping_label(all_item_ids)` per store group; on failure, fallback only failed targets to per-order PrintAWB/GetDocument with bounded concurrency. Does not downgrade bulk successes.

## Products

- Primary UX: **+ Copy Product** → Connected Item ID **or** Daraz Product Link.
- `POST /api/products/clone-draft/from-connected` — one `/product/item/get`, no catalog sync.
- Sync Products defaults to `fetch_details=False` (catalog-only). Lazy detail via `ensure_product_detail` / Prepare Copy / direct fetch.
- `POST /api/products/import-url/draft` — SSRF-safe daraz.pk public extract → shared ProductCloneDraft pipeline.

## Tests / build (this session)

- Backend pytest: see latest run
- Frontend vitest / `tsc` / Vite: unchanged for this hotfix
