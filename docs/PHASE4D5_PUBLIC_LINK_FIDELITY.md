# Phase 4D.5 — Public-Link Product Fidelity

## 1. Variant Price Root Cause

Modern Daraz public PDPs **strip per-SKU prices from SSR `skuInfos`**. Live bag product (`i433265727`) exposed 4–5 variants with identity/images/categoryId only — **no `price` key**.

That is why Add Product asked for **"Try price override"**: every variant had `price=None`, so validation returned `missing_price`.

The previous single `price_override` then **flattened** one value onto every SKU — unsafe for differently priced variants.

## 2. Variant Pricing

**Source → normalized → CreateProduct**

| Stage | Representation |
|-------|----------------|
| Public extract | Per variant: `daraz_sku_id`, `sale_props`, `price` (regular), `special_price`, `price_confidence`, `price_source` |
| Draft | Same fields + generated `seller_sku` (MTF-…) |
| CreateProduct XML | Per `<Sku>`: `<price>`, optional `<special_price>` + window |

Example:

```
Variant A: regular=1000 special=799
Variant B: regular=1200 special=999
→ two independent <Sku> nodes — never flattened
```

Prices are recovered from (in order): `skuInfos` structured price → `skuBase` identity + merge → catalog `originalPrice` (cheapest SKU only) → `pdt_price` / JSON-LD (single-SKU only).

## 3. Price Override

| Case | Behavior |
|------|----------|
| Single SKU | Single override allowed |
| All variants same regular price | Single override applies to all |
| Differently priced variants | **Single override refused** — never flattens |
| Some missing | Per-variant `variant_price_overrides` for unresolved keys only |

## 4. Special Price

- **Connected / seller API**: copy `special_price` (+ dates) from GetProductItem.
- **Public page**: only when structured `skuInfos` exposes seller special fields — **catalog display/`price` is ignored** (often campaigns/vouchers/coins).
- CreateProduct XML includes `<special_price>` when present.
- Dedicated post-create write via `/product/price/update` (`SalePrice`).

## 5. Special Price Write API

1. **CreateProduct** — include per-SKU `special_price` / `special_from_time` / `special_to_time` in form-body XML (Phase 4D.4 transport unchanged).
2. **UpdatePrice** (`POST /product/price/update`) — second step for seller Special Price; failure → `CREATED_WITH_WARNING`, **not** recreate.

## 6. Category Mis-Resolution

Public drafts previously left `primary_category_id=None` (name hint only).

Now leaf ID is taken from:

1. Catalog ajax `categories[-1]` (high confidence)
2. `skuInfos.categoryId`
3. `regCategoryId` (medium)

Ambiguous / missing → `NEEDS_ATTENTION` (`category_unresolved`) — no random category.

## 7. No Brand

Unresolved or missing public brand → destination category **No Brand** via `/category/brands/query` (cached per call). Valid brand name/id used — not a blind `"No Brand"` invent when the API returns a row. If category has no No Brand fallback → still `NEEDS_ATTENTION`.

## 8. Duplicate Prevention

Order (local catalog / index only):

1. Existing local title exact / high-score match → `ALREADY_EXISTS`
2. Title similar / category+title partial → `POSSIBLE_DUPLICATE`
3. Normalized title + keyword Jaccard (`normalize_product_title` / `product_title_tokens`)
4. After successful create → immediate local catalog upsert

No full Product Sync before Add Product.

## 9. Duplicate Performance

- Uses `find_possible_product_duplicates` on local DB/memory index.
- Instrumented `duplicate_check_ms` on destination timings.
- Tests assert `get_products` (full catalog sync) is **not** called on the duplicate path.
- Newly created products are upserted immediately so the next Add sees them.

## 10. Public Link Happy Path

Paste link → extract variants/prices/category/brand → local duplicate check → resolve brand/No Brand → CreateProduct (form transport) → UpdatePrice specials → verify → local catalog upsert.

## 11. Needs Attention (only)

- Unresolved per-variant regular prices
- Invalid special (≥ regular, ≤0)
- Category unresolved / attributes invalid
- Brand mandatory with no No Brand fallback
- Images cannot resolve
- Ambiguous `POSSIBLE_DUPLICATE` (unless `allow_duplicates`)

## 12. Tests

| Suite | Count |
|-------|------:|
| Backend pytest | **206 passed** |
| Frontend vitest | **28 passed** (5 files) |

New: `tests/test_phase4d5_fidelity.py` (variant pricing, override non-flatten, special XML, brand No Brand, duplicates, multi-store).

## 13. Build

| Check | Result |
|-------|--------|
| `tsc --noEmit` | **pass** |
| Vite production build | **pass** (`✓ built in 34.93s`) |

## 14. Live Testing

**Do not auto-create.** Safe for manual post-deploy tests:

- **A.** Multi-variant public-link product (confirm per-SKU prices / Needs Attention for missing ones)
- **B.** Already-existing product title on destination (`ALREADY_EXISTS`, no second create)

## 15. Remaining Limitations

- Many public PDPs still omit per-SKU prices in SSR — those SKUs require seller input (or connected ownership upgrade).
- Catalog listing `price` is **not** copied as Special Price (promo contamination risk).
- UpdatePrice path depends on Daraz accepting `/product/price/update` for the seller app — failures warn without recreating.
- Category leaf from catalog assumes last `categories[]` entry is the sellable leaf.
