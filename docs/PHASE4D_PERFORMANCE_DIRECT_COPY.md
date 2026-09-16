# Phase 4D — Core Performance + Direct Product Copy + Public Link Import

Status: implemented locally. Create Copy remains gated (Phase 4C supervised A→B create not completed — single connected store).

## Shipping

- `POST /api/shipping/rts`: bounded concurrency (`STORE_CONCURRENCY=3`), live `/orders/get` only, header upsert + batched print-history reconcile. Returns `timings_ms` per store and wall-clock total.
- Print path: `hydrate_missing_order_items` before validation; every selected order gets an explicit outcome; print events only on SUCCESS after merge; UI shows `N/M` + **Retry Failed**.
- Label fetch: per-store grouping + bounded concurrent PrintAWB/GetDocument (`PRINT_FETCH_WORKERS`, default 8). Per-target fallback on Daraz/document errors (does not downgrade the whole batch).

## Products

- Primary UX: **+ Copy Product** → Connected Item ID **or** Daraz Product Link.
- `POST /api/products/clone-draft/from-connected` — one `/product/item/get`, no catalog sync.
- Sync Products defaults to `fetch_details=False` (catalog-only). Lazy detail via `ensure_product_detail` / Prepare Copy / direct fetch.
- `POST /api/products/import-url/draft` — SSRF-safe daraz.pk public extract → shared ProductCloneDraft pipeline.

## Tests / build (this session)

- Backend pytest: **160 passed**
- Frontend vitest: **26 passed** (5 files)
- `tsc --noEmit`: **0**
- Vite production build: **OK**
