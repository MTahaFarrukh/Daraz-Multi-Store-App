# Phase 4B — Product Hub + Clone Foundation

**Date:** 2026-09-16  
**Scope:** Connected-store Product Hub, local warehouse, clone drafts, gated create.  
**Not in scope:** Public URL scraping, Communities, Inventory/Finance, unrestricted Create Copy.

---

## Overall result

**DELIVERED** — Product Hub + connected-store clone foundation is implemented and tested.  
**CreateProduct:** payload preview exists; live create remains **NOT RUN** (gated).  
**Image migrate poll → new platform URL:** **NOT PROVEN** (`/image/response/get` → `E005`); own-store CDN URLs can be reused as a temporary fallback after migrate submit.

---

## Files created/modified

### Created
- `src/seller_sku.py`
- `src/package_resolve.py`
- `src/description_enhance.py`
- `src/image_migrate.py`
- `src/product_sync.py`
- `src/product_clone.py`
- `frontend/src/pages/ProductsPage.tsx`
- `frontend/src/hooks/queries/useProducts.ts`
- `frontend/src/lib/productQueryKeys.test.ts`
- `tests/test_phase4b_products.py`
- `tests/test_products_repo.py` (repo warehouse)
- `scripts/phase4b_image_migrate_proof.py`
- `docs/PHASE4B_PRODUCT_HUB.md` (this report)
- `data/phase4b_image_migrate_proof.json`

### Modified
- `src/db/schema.sql` — `daraz_products`, `daraz_product_variants`, `workspace_product_defaults`
- `src/db/repo.py` — product warehouse + defaults + duplicate finder
- `src/daraz_api.py` — product/category/image/create client methods
- `src/app.py` — products / defaults / clone-draft / create-probe APIs
- `frontend/src/App.tsx`, `PlaceholderPages.tsx`, `SettingsPage.tsx`
- `frontend/src/lib/api/index.ts`, `types/api.ts`, `lib/queryKeys.ts`
- `.env.example` — `ALLOW_PRODUCT_CREATE_PROBE` note

---

## Database schema

- **`daraz_products`** — workspace/store scoped; UNIQUE `(store_id, daraz_item_id)`; attributes/variation/images JSONB; `video_ref` informational
- **`daraz_product_variants`** — UNIQUE `(store_id, daraz_sku_id)`; sale props, prices, qty, package L/W/H/weight
- **`workspace_product_defaults`** — package defaults (kg/cm), `default_initial_quantity`, `sku_prefix` (default `MTF-`)

---

## Product sync

**Strategy for 408+ products/store:**

1. Page `GET /products/get` (`filter=all`, `limit=50`, `options=1`) until exhausted (`MAX_OFFSET=20000`).
2. List payload is rich; call `GET /product/item/get` only when multi-SKU / missing variation / missing video.
3. Detail calls use **bounded concurrency = 4** (ThreadPoolExecutor) — no unbounded fan-out.
4. Per-store isolation + idempotent upserts.
5. Normal Product Hub list/search hits **local DB only**.

---

## Product Hub

`/app/products`: Sync Products, All Stores / Status / Category / Search, paginated table with image, title, **source store**, variants, price range, stock, status, last sync, Copy.

---

## Product detail

Dialog: gallery, title, store, item id, category, brand, status, description HTML, variants (SellerSku, saleProp, price, qty, weight, L×W×H), video warning if `video_ref` present.

---

## Product defaults

Settings → Product Defaults (kg/cm + initial quantity). Persisted via `PUT /api/product-defaults`.

---

## SellerSku generation

Central `src/seller_sku.py`:

- Prefix `MTF-` (workspace-configurable later via `sku_prefix`)
- Uppercase normalized tokens
- Variant-aware: `MTF-{PRODUCT}-{VARIANT}` then `-2`, `-3` on collision
- Max length 50

**Examples:**

| Input | Output |
|-------|--------|
| title `Dumpling Glow Toy`, saleProp `{color_family: Glow}` | `MTF-DUMPLING-GLOW-TOY-GLOW` (truncated/normalized) |
| collision | `…-2` |
| Live catalog already uses | `MTF-POPSICLE-Squishy`, `MTF-BUTTER-SQUISHY` (4A proven valid) |

Source SellerSku is **never** copied into destination draft.

---

## Package resolution

`src/package_resolve.py` — single resolver:

**Connected store:** valid source SKU value → else workspace default → else validation error (no zero).  
**Future public/community:** workspace default only.

Units from 4A live payloads: **kg** weight, **cm** dimensions.

---

## ProductCloneDraft

`POST /api/products/{id}/clone-draft` → representative shape (redacted):

```json
{
  "source_type": "connected_store",
  "source_product_id": "…",
  "destination_store_uuid": "…",
  "product": {
    "title_en": "…",
    "primary_category_id": 10002730,
    "brand": "No Brand",
    "description_html": "<p>…</p><div data-multistore-desc-images>",
    "attributes": { "warranty_type": "No Warranty" }
  },
  "media": {
    "product_images": ["https://static-01.daraz.pk/p/…"],
    "video": { "source_id": "7100…", "status": "unsupported" },
    "migration_status": "pending"
  },
  "variants": [{
    "seller_sku": "MTF-…",
    "price": 999,
    "quantity": 1,
    "package_weight": 0.2,
    "sale_props": { "color_family": "Glow" }
  }],
  "validation": {
    "fidelity": {
      "copied": ["Title", "Category", "Variants", "…"],
      "changed_by_multistore": ["SellerSku → generated MTF-…", "Quantity → workspace initial"],
      "not_available": ["Video"]
    },
    "can_create": true,
    "create_gated": true
  }
}
```

**Quantity policy:** workspace `default_initial_quantity` (default **1**). Source stock shown as warning, not copied.

---

## Description enhancement

`src/description_enhance.py`: sanitize allowlisted HTML; if no description images, append up to 6 product images; if images exist, preserve and avoid obvious duplicates.

---

## Duplicate detection

Soft match on destination by normalized title / token overlap (+ category boost). Returned as `possible_duplicates` warnings — does not block draft.

---

## Image migration proof

| Step | Result |
|------|--------|
| `POST /images/migrate` with owned CDN URL | **OK** — returns `batch_id` |
| Poll `GET /image/response/get?batch_id=…` | **FAIL** — `E005: Invalid Request Format` (repeated) |
| Final migrated URL from poll | **NOT obtained** |
| Timeout fallback | Reuses source Daraz CDN URL with status `timeout` |

**Critical:** batch polling did **not** reach a proven new platform-hosted URL.  
For already-`static-01.daraz.pk` images, reusing the source URL may be acceptable for create payloads, but this is **not** a complete migrate-poll proof for arbitrary external images.

Evidence: `data/phase4b_image_migrate_proof.json`.

---

## Media preparation

`DarazImageMigrationService` implemented with cache + statuses:  
`submitted` / `processing` / `completed` / `failed` / `timeout`.  
Wired for reuse; draft still marks `migration_status: pending` until poll contract is solved.

---

## CreateProduct payload generation

`POST /api/products/{id}/create-payload-preview` returns redacted preview:

```json
{
  "PrimaryCategory": 10002730,
  "Attributes": {
    "name_en": "…",
    "brand": "No Brand",
    "description_en": "[html redacted — length N]"
  },
  "Images": ["https://static-01.daraz.pk/p/…"],
  "Skus": [{ "SellerSku": "MTF-…", "price": 999, "quantity": 1, "package_weight": 0.2 }]
}
```

---

## Supervised CreateProduct proof

**NOT RUN**

Reasons:

1. `ALLOW_PRODUCT_CREATE_PROBE` defaults **false** (403 when called).
2. Image migrate → final URL poll remains unproven (`E005`).
3. Endpoint intentionally returns `status: NOT_RUN` even when flag is on, until an operator-run dedicated create script is approved — no automatic live create during tests/startup.

No product ID / QC state to report.

---

## Public URL status

Still deferred. No scraping. No “Add from Daraz Link” pretend-import.

---

## Community status

Still deferred. Draft `source_type` enum reserved; only `connected_store` active. No cross-workspace reads.

---

## Security

- Auth + workspace membership on all product routes
- Source/destination stores must belong to workspace
- Same-store clone rejected
- Cross-workspace source blocked
- Tokens never returned
- Create probe disabled by default
- Description HTML sanitized (no scripts/iframes)

---

## Tests

| Suite | Count |
|-------|-------|
| Backend full `pytest` | **118 passed** |
| Frontend `vitest` | **19 passed** |

Includes: repo isolation/upsert/pagination, MTF SKU + collision, package priority, description sanitize/append, image migrate mock success/fail, clone draft, defaults API, create probe 403.

---

## Build

Frontend production build: **OK** (`vite build`, exit 0).  
TypeScript `tsc --noEmit`: **OK**.

---

## Risks / technical debt

1. `/image/response/get` contract still wrong/unknown → blocks confident CreateProduct for non-Daraz-hosted images.
2. CreateProduct XML schema not end-to-end proven.
3. Category attribute validation before create is draft-level only (not full `/category/attributes/get` gate yet).
4. Brand id resolution not wired into draft (brand string copied).
5. Only one connected store commonly available locally → clone UX needs ≥2 stores.
6. Product detail uses `dangerouslySetInnerHTML` on stored description (sanitized on enhance path; raw detail still from Daraz HTML — consider sanitize on display).

---

## Recommendation

### Answers

1. **Is connected-store cloning technically ready for general activation?**  
   **No** for live Create. **Yes** for Hub + draft/preview.

2. **Are images fully cloneable?**  
   **Partial.** Own CDN images usable; migrate-poll for final new URLs **not proven**.

3. **Are description images ready?**  
   **Draft-ready** (sanitize + append). Final create still needs accepted image URLs.

4. **Are variants preserved?**  
   **Yes** in local warehouse + draft (`sale_props`, prices, packages).

5. **Are source package weight/dimensions preserved?**  
   **Yes** for connected source when present; else workspace defaults.

6. **Are MTF SellerSkus valid/generated correctly?**  
   **Yes** — generator enforced; live catalog already accepts `MTF-`.

7. **What remains before exposing “Create Copy”?**  
   - Prove `/image/response/get` (or alternate) → usable migrated URL  
   - Prove one supervised CreateProduct under `ALLOW_PRODUCT_CREATE_PROBE`  
   - Category mandatory-attribute validation against destination category  
   - Robust brand resolution  
   - Then enable gated Create in UI

### Next step (4C suggestion)

Focused media+create spike only — no Communities, no scraping.

---

**STOP.** Public URL scraping and Communities not started.
