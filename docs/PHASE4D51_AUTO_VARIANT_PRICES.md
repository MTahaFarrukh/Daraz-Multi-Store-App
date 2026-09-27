# Phase 4D.5.1 — Automatic Public Variant Prices

## A. Exact Daraz Price Source

When SSR `skuInfos` omits `price`, the **live Daraz PDP** hydrates via:

**`mtop.global.detail.web.getDetailInfo`** on `acs-m.daraz.pk`

Browser payload (from PDP inline script):

```js
{
  deviceType: "pc",        // or android / ios
  path: window.location.href,
  uri: pathname,
  headerParams: JSON.stringify({ "user-agent": ... }),
  cookieParams: JSON.stringify(document.cookie map),
  requestParams: JSON.stringify(URLSearchParams)
}
```

Signed H5 mtop POST (`appKey=24677475`, `_m_h5_tk` MD5 sign).

Success response: `data.module` (JSON string) → fields including **`skuInfos[skuId].price`**:

```json
"price": {
  "originalPrice": { "value": 8500, "text": "Rs. 8,500" },
  "salePrice": { "value": 2518, "text": "Rs. 2,518" },
  "discount": "-70%"
}
```

**Evidence:** live call against bag product `i433265727` returned `SUCCESS` with 4 SKUs, each with `originalPrice`/`salePrice`. One response covers the full matrix (no per-variant XHR).

## B. Old Failure

SSR `skuInfos` had identity only → `price=None` → `missing_price` → NEEDS_ATTENTION → “enter prices…”. Prices existed; we simply never called the PDP’s hydration API.

## C. New Extraction Chain

1. SSR / structured page (`skuBase` + `skuInfos`)
2. If any SKU regular price missing → **`mtop.global.detail.web.getDetailInfo`** (one call, all SKUs)
3. Merge by Daraz SKU ID
4. Catalog / `pdt_price` hints (single-SKU / metadata only — **never flatten**)
5. Validate → CreateProduct  
Manual UI only if all of the above still leave gaps.

## D. Variant Mapping

Merge key order: **Daraz SKU ID** → never array index. `sale_props` from `skuBase.propPath` remain the variation labels.

## E. Example

After getDetailInfo (illustrative different originals):

| Color  | Regular |
|--------|--------:|
| Black  | 1000 |
| Pink   | 1200 |
| Purple | 1100 |
| Green  | 1300 |

Live bag product: all four `originalPrice=8500` (auto-resolved; no Needs Attention).

## F. Multi-Dimensional Variants

Color × Size = distinct `skuId` rows. Each combination gets its own `originalPrice` from detail `skuInfos`.

## G. Special Price

- `originalPrice` → **regular**
- `salePrice` kept as `current_price` metadata only — **not** seller Special Price (often campaign)
- Seller special still only from connected GetProductItem / proven fields → CreateProduct + `/product/price/update`

## H. Performance

Live bag product (dev, one run):

| Stage | ms |
|-------|---:|
| `ssr_fetch_ms` | ~2508 |
| `structured_parse_ms` | ~24 |
| catalog | ~2033 |
| `price_resolution_ms` | ~2275 |
| `fallback_fetch_ms` (signed detail POST) | ~423 |
| `total_source_extract_ms` | ~6841 |

Cached detail payload TTL 30m; public extract cache unchanged.

## I. Multi-Store

`fetch_public_product` still runs **once** per Add; draft reused per destination. Detail cache keyed by item_id.

## J. Manual Fallback

Only when SSR + getDetailInfo + safe catalog hints still leave unresolved SKU regulars (API fail / token fail / empty module).

## K. Tests

| Suite | Count |
|-------|------:|
| Backend pytest | **213 passed** |
| Frontend vitest | **28 passed** |

New: `tests/test_phase4d51_auto_prices.py`.

## L. Build

| Check | Result |
|-------|--------|
| `tsc --noEmit` | **pass** |
| Vite production build | **pass** |

## M. Live Testing

Do **not** auto-create. Safe to manually retry the **same multi-variant public product** after deploy — prices should resolve automatically (no “enter prices” for that failure class).
