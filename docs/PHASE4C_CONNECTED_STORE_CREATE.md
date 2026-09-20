# Phase 4C — Connected Store Create Proof

**Date:** 2026-09-16  
**Scope:** Unblock image poll, CDN reuse, category/brand validation, description sanitization, gated CreateProduct payload + supervised path.  
**Constraint:** General Create Copy remains gated. No Communities / public URL import.

---

## Image migration E005 root cause

**Exact cause:** `POST /images/migrate` was sending singular XML wrapper `<Request><Image><Url>…</Url></Image></Request>`.

Daraz PK accepts that payload and returns a `batch_id`, but that `batch_id` is **not valid for poll** — `GET /image/response/get` returns `E005: Invalid Request Format`.

Official batch contract requires plural:

```xml
<Request><Images><Url>…</Url></Images></Request>
```

Confirmed: same poll endpoint + `batch_id` param succeeds when migrate used `<Images>`.

---

## Correct image poll contract

| Step | Contract |
|------|----------|
| Batch migrate | `POST /images/migrate` + business param `payload` = XML with **`<Images>`** |
| Wait | ≥ ~0.5–1s before first poll |
| Poll | `GET /image/response/get` only (POST → `UnsupportedHTTPMethod`) |
| Param | `batch_id` snake_case (`batchId` → `MissingParameter`) |
| Success | `code=0`, `data.images[].url` |

**Singular alternate (no poll):** `POST /image/migrate` + `<Image><Url>` → immediate `data.image.url`.

Evidence: `data/phase4c_image_poll_probe.json`, `data/phase4c_image_migrate_proof.json`.

---

## Final image URL proof

**PROVEN**

Owned CDN URL → migrate (`<Images>`) → `batch_id` → poll → `data.images[0].url`  
(returned same `https://static-01.daraz.pk/p/…` URL for already-hosted assets).

---

## Existing Daraz CDN reuse

**PROVEN** (resolver strategy)

- `is_daraz_product_cdn_url` → `reuse_cdn` without calling migrate.
- Singular migrate of own CDN URL returns the same URL (identity).
- CreateProduct acceptance of reused CDN URLs across stores: **NOT PROVEN end-to-end** (no second destination store for live create).

---

## Category validation

Implemented in `src/category_validate.py`:

- Loads `/category/attributes/get`
- Compares mandatory normal + SKU fields vs ProductCloneDraft
- Returns `valid`, `missing_required`, `invalid_values`
- Supervised create **blocks** when mandatory requirements are missing

---

## Brand resolution

Implemented in `src/brand_resolve.py` via `/category/brands/query`:

| Outcome | Behavior |
|---------|----------|
| `EXACT_MATCH` | Use destination brand name (+ `brand_id` when present) |
| `NO_BRAND` | Use destination `No Brand` representation |
| `UNRESOLVED` | Error — require operator correction; **never invent brand IDs** |

---

## Description sanitization

- Backend: `sanitize_description_html` (strips script/iframe/handlers/`javascript:`; keep safe Daraz/Lazada images)
- API: `description_html_safe` / `short_description_html_safe` on product views
- Frontend: shared `frontend/src/lib/sanitizeHtml.ts` before `dangerouslySetInnerHTML`
- Regression tests backend + Vitest

---

## Final CreateProduct payload

Builder: `src/product_create_payload.py` (`build_create_product_xml` + redacted preview).

Representative redacted preview:

```json
{
  "PrimaryCategory": 10002730,
  "Attributes": {
    "name": "…",
    "name_en": "…",
    "brand": "No Brand",
    "brand_resolution": "EXACT_MATCH|NO_BRAND|…",
    "description_en": "[html redacted — length N]",
    "warranty_type": "No Warranty"
  },
  "image_count": 1,
  "Images": ["https://static-01.daraz.pk/p/…"],
  "variant_count": 1,
  "Skus": [
    {
      "SellerSku": "MTF-…",
      "price": 999,
      "quantity": 1,
      "package_weight": 0.2,
      "package_length": 10,
      "package_width": 5,
      "package_height": 4,
      "saleProp": {"color_family": "…"}
    }
  ],
  "validation_status": { "can_create": false, "category_valid": true, "…": "…" }
}
```

Quantity policy unchanged: workspace `default_initial_quantity` (default **1**), not live source stock.

---

## Supervised CreateProduct

**NOT RUN**

Exact reason: only **1** connected store in the local vault (`mtfdigitalemporiumofficial_gmail_com`). No safe second destination store. Per phase rules: **do not create anything**.

Gating kept:

- `ALLOW_PRODUCT_CREATE_PROBE=false` by default
- `POST /api/products/{id}/create-probe` requires flag + `confirm=true` + `execute=true` for live call
- Never invoked from startup / normal Product Hub browse

Evidence: `data/phase4c_supervised_create.json`.

---

## Description images

Not verified in a live CreateProduct (create **NOT RUN**).  
Draft enhancement + sanitization path remains in place for when a second store is available.

---

## Variants

Draft + XML builder preserve sale props, prices, package dims, and regenerate MTF SellerSkus. Live response comparison **NOT RUN**.

---

## MTF SellerSkus

Generated in clone draft / payload preview (unit-proven), e.g. `MTF-DUMPLING-…`.  
Source SellerSku never copied into destination payload XML.

---

## Package Weight/Dimensions

Connected-source values preserved in draft/payload when present; otherwise workspace defaults. Live create confirmation **NOT RUN**.

---

## Tests

| Suite | Result |
|-------|--------|
| Backend pytest | **142 passed** |
| Frontend vitest | **22 passed** (4 files) |

Coverage includes image poll success/processing/failure/timeout, CDN reuse, external migrate, `<Images>` wrapper, category/brand gates, HTML sanitization, payload/MTF, create flag off, cross-workspace, duplicate safety.

---

## Build

| Check | Result |
|-------|--------|
| `tsc --noEmit` | OK |
| Vite production build | OK |

---

## Remaining blockers

1. **No second connected destination store** → cannot complete supervised CreateProduct proof.
2. CreateProduct CDN-reuse acceptance **across stores** still needs one live A→B create.
3. External (non-Daraz) image migrate proven at poll URL level for owned CDN; external hosts still need a real external URL sample when available.
4. General Product Hub “Create Copy” intentionally still gated.

---

## Recommendation

1. **Can we safely expose connected-store "Create Copy" now?**  
   **No.** Keep gated until one supervised A→B create succeeds.

2. **Can existing Daraz-hosted images be cloned reliably?**  
   **Resolver: yes (reuse_cdn PROVEN).** Cross-store CreateProduct acceptance: pending live create.

3. **Can external images be migrated reliably?**  
   **Poll contract PROVEN.** External host end-to-end should be re-checked with a non-Daraz URL under supervision.

4. **Can description images be created reliably?**  
   Draft-ready; live CreateProduct acceptance **NOT RUN**.

5. **Are category + brand requirements sufficiently validated?**  
   **Yes for pre-submit gating** (mandatory attrs + exact brand match). Live QC after create still unproven.

**STOP.** Do not enable general Create Copy without review. Do not implement public URL import or Communities in this phase.
done
