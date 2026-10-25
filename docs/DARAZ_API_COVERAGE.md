# Daraz / Lazada product API coverage (read-only prep)

Status date: 2026-05-25 (local codebase inventory).  
**No CreateProduct / UpdatePriceQuantity / MigrateImages / Deactivate / Remove is enabled by the new diagnostic paths.**

Legend: **used** = called from app code; **unused** = client wrapper may exist but not wired into create/diagnostic flows; **unverified** = not live-proven on Daraz PK in this repo.

| Endpoint | Path | Used? | Parameters we send | R/O vs mutating | Notes / mismatches |
|---|---|---|---|---|---|
| GetBrandByPages | `GET /category/brands/query` | **used** | `startRow`, `pageSize`, optional `primary_category_id`, optional `name` | read-only | Live PK: `name` often ignored; ordering unreliable; `total_record` can be ~78k. Deep scan does not use `name`. |
| GetCategoryAttributes | `GET /category/attributes/get` | **used** | `primary_category_id` | read-only | Create prep validation. |
| GetCategoryTree | `GET /category/tree/get` | **used** (elsewhere) | (none beyond auth) | read-only | Not part of No Brand deep scan. |
| GetSellerItemLimit | `GET /product/seller/item/limit` | **used** (dry-run preflight) | **none** (auth only) | read-only | Lazada typings: `data.onlineItemCount`, `data.itemLimit`. **Daraz PK response shape unverified.** Doc ~10 QPS/seller — callers must not spam. |
| GetProductItem | `GET /product/item/get` | **used** | `item_id` **or** `seller_sku` | read-only | Spike proved both params. Reconcile uses both; never auto-retries CreateProduct. |
| GetProducts | `GET /products/get` | **used** | `filter`, `offset`, `limit`, `options`, optional `sku_seller_list`, `search`, date filters | read-only | Reconcile + catalog sync. |
| GetQcStatus | `GET /product/qc/status/get` | **optional** after reconcile verify | `seller_skus` (JSON array string), `offset`, `limit` | read-only | **Unverified on Daraz PK.** Failures ignored for reconcile outcome. Legacy Seller Center used `SkuSellerList` — different surface. |
| GetCategorySuggestion | `GET /product/category/suggestion/get` | **unused** | — | read-only | Documented elsewhere; not wired here. |
| MigrateImage | `POST /image/migrate` | **used** (gated create only) | XML `payload` with `<Image><Url>` | **mutating** | Dry-run / deep-scan / diagnostic routes never call. |
| MigrateImages | `POST /images/migrate` | **used** (gated create only) | XML `payload` with `<Images>` ≤8 URLs | **mutating** | Dry-run uses public HTTP preflight only. |
| GetResponse | `GET /image/response/get` | **used** (migrate poll) | `batch_id` | read-only | Poll after batch migrate. |
| CreateProduct | `POST /product/create` | **used** (gated) | form `payload` XML | **mutating** | Still gated by `ALLOW_PRODUCT_CREATE` / probe flags. Requires verified `NO_BRAND`; sends `brand_id` when verified. |
| UpdatePrice | `POST /product/price/update` | **used** (post-create specials) | form `payload` XML | **mutating** | Not part of dry-run. |
| UpdatePriceQuantity | — | **unused** | — | mutating | **Not implemented** (max 50 / 20 recommended per Lazada docs — noted only). |
| Deactivate / Remove | — | **unused** | — | mutating | **Not implemented.** |
| GetSeller | `GET /seller/get` | **used** (health) | (none) | read-only | Store connectivity probe. |

## Brand payload notes

- Lazada Create Product docs: `brand` deprecated; `brand_id` mandatory from GetBrandByPages; brand lists differ per country — **never reuse brand_id across marketplaces**.
- This codebase sends `brand_id` when a verified id exists and still includes `brand` name for compatibility with existing fixtures (both accepted in our XML builder tests — **live dual-field acceptance on Daraz PK remains unverified**).

## Safety invariants

1. Diagnostic deep scan (`deep=true`) ≤ ~60 pages/request, sequential delay, resumable via `scan_from_start_row` / `next_start_row`.
2. Create-path brand resolve budget remains `_MAX_LOOKUP_CALLS = 48`.
3. Dry-run: zero MigrateImage / CreateProduct / UpdatePrice calls.
4. Definite brand rejection (`Brand:C011`) blocks corroboration for `(store, category)` until human clear.
5. Migration file `src/db/migrations/verified_category_no_brand.sql` is **not** auto-applied.
