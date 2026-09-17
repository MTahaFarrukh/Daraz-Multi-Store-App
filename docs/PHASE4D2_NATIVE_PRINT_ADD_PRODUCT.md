# Phase 4D.2 — Native Printing + One-Click Product Listing

## Shipping

- Print Labels no longer awaits `/api/print-labels/validate` (hydrate stays inside the job).
- Select Unprinted → immediate print; Select All with prior prints → client-only warning → Print Selected N.
- Default `PRINT_PREFER_NATIVE_PDF=1`: PrintAWB first; bulk GetDocument only when pages match; HTML convert is per-doc fallback.
- `DOCUMENT_MAPPING_FAILED` when merged pages &lt; credited successes — no print events for excess.

## Products

- Primary UI: **+ Add Daraz Product** (URL or connected Item ID → multi-store → Add).
- `POST /api/products/add-from-url` / `add-from-connected` — fetch once, per-dest create.
- Gate: `ALLOW_PRODUCT_CREATE` or probe. Supervised live CreateProduct still requires human-enabled env + confirm.

## Validation (local)

- Backend: **181 passed**
- Frontend: see latest vitest / tsc / Vite run
