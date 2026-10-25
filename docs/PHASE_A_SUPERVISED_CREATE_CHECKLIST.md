# Phase A — Supervised live CreateProduct proof checklist

**Status:** Prepared only. **Do not run a live create** until an operator explicitly approves the final step below.

**Goal:** One product → one explicitly selected destination store, with create gates, claims, and reconciliation respected.

This checklist does **not** enable `ALLOW_PRODUCT_CREATE` or `ALLOW_PRODUCT_CREATE_PROBE` by itself. Keep both **off** in production until proof succeeds under supervision.

---

## Preconditions (read-only)

1. Destination store is connected in the **same** workspace as the operator.
2. Run **Check API (read-only)** on the destination (`POST /api/stores/{id}/health-check` → `/seller/get`). Confirm `api_health=ok`. Token-OK alone is insufficient.
3. Source is either:
   - a connected product in the same workspace, or
   - a public Daraz URL (no cross-workspace peer catalog unless `copy_products` is granted).
4. Confirm create flags are still gated (Phase A2 — flags are isolated):
   - Prefer probe path: `ALLOW_PRODUCT_CREATE_PROBE=true` temporarily, **`ALLOW_PRODUCT_CREATE=false`**.
   - `ALLOW_PRODUCT_CREATE_PROBE` does **not** unlock ordinary Add Product, URL, connected-product, or bulk creation.
   - Use `POST /api/products/{id}/create-probe` only (claims + mapping + reconcile attempts).
   - Full gate `ALLOW_PRODUCT_CREATE=true` only after probe proof, if ordinary create UI is needed.
5. Confirm no open **uncertain** create attempt / held claim for the same source→destination identity. If present: **reconcile first** — never create again blindly.

---

## Pre-submit verification (dry-run)

Use **create-probe** with **`execute=false`** (mutation-free: no CreateProduct, UpdatePrice, or image migrate). Confirm before any live call. Optional: `GET /api/stores/{id}/category-brands/no-brand?primary_category_id=…` for read-only brand diagnostics.

| Check | Expected |
|-------|----------|
| Destination | Exactly one selected store; label matches intended shop |
| Category | Primary category resolves; required attrs valid |
| Brand | Destination **No Brand** resolved from category brands query (`status=NO_BRAND`, verified name — never a guessed ID) |
| Price | Sale / special prices match intent; overrides applied if used |
| SKU | MTF SellerSkus generated; source SellerSku not reused |
| Quantity | Per-variant qty present and intentional |
| Package | Weight / L / W / H present (source or workspace defaults) |
| Images | Resolved CDN/migrate URLs; no unresolved image errors |
| Duplicates | No strong destination duplicates (or explicit `allow_duplicates` with operator intent) |
| Mapping / claim | No verified mapping already; no uncertain attempt; claim free |
| Permissions | Same-workspace OK; peer source requires ACCEPTED + `copy_products=true` |

Record the dry-run preview payload / correlation id. **Do not proceed** if brand is `UNRESOLVED` / `no_brand_unavailable`.

---

## Explicit approval gate (human)

**Stop here until the operator types approval in chat / change ticket.**

Required approval phrase (example):

> APPROVED: supervised create of item \<source_id_or_url\> into store \<store_id\> only. execute=true once.

Without that approval: leave `execute=false` / flags off. Automated tests must never call live CreateProduct.

---

## Live submission (once)

1. Enable the chosen gate for the session only.
2. Submit **one** create with `confirm=true` and `execute=true` (probe) or gated Add Product for that single destination.
3. Capture: attempt id, correlation id, Daraz response (redacted), destination item id if returned.

---

## After submission — reconcile before any retry

| Outcome | Action |
|---------|--------|
| Clear success + destination item id | Record verified mapping; sync destination catalog; stop |
| Error with certain failure (validation / rejected, no create) | Safe to fix draft and retry **after** claim release |
| Timeout / unknown / missing item id / network cut | Mark **uncertain** — run reconcile; **never auto-retry CreateProduct** |
| Uncertain claim held | Reconcile endpoint only; do not start another create for same pair |

Never automatically retry an uncertain create request.

---

## Rollback / cleanup of flags

1. Set `ALLOW_PRODUCT_CREATE_PROBE=false` and `ALLOW_PRODUCT_CREATE=false` after the session unless a follow-on plan is approved.
2. Do not leave probe enabled on Render “for convenience.”

---

## Out of scope for this proof

- Bulk copy jobs  
- Stock / price / order mutations  
- Pack / RTS  
- Cross-workspace copy without `copy_products`  
- Deploy / push without separate approval  

---

## Related code

- Gates: `src/product_create.py` (`create_probe_enabled`, `product_create_enabled`, `run_supervised_create`)
- Add / URL / connected: `src/product_add.py` (claims + mapping + copy permission)
- Bulk: `src/product_bulk_copy.py` (uncertain → reconcile, no blind recreate)
- Claims / mapping: `src/product_copy_mapping.py`, `src/product_create_reconcile.py`
- Brand: `src/brand_resolve.py` (verified No Brand only)
