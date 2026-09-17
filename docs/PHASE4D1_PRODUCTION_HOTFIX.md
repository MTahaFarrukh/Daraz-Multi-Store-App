# Phase 4D.1 — Production Hotfix

## Failures addressed

1. **MTF Digital Emporium ~43.5s Load RTS** — one slow `/orders/get` under 180-day `created_after` (not retries; timeout 45s, no client retries). Primary window now `update_after` 90d with expand to `created_after` 180d if empty; per-page `request_timings`; pagination stop when count fits one page; batch print reconcile.

2. **28/21/7 → Print 0 Unprinted** — HITL used post-validate `new_printable` and dropped unhydrated unprinted into `not_eligible`. Fixed: HITL from `partitionPrintSelection` (print events); validate hydrates first and exposes `unprinted_ids`/`printed_ids`; Print 7 / Reprint All 28.

3. **Products 500** — `list_daraz_products` SELECT omitted `catalog_seen_at` / `detail_*` but `_product_row` expected them → `IndexError`. SELECT aligned with `_PRODUCT_SELECT`.

4. **Print speed** — restore bulk `get_shipping_label(all_item_ids)` per store (default on); per-order fallback only on failure.

## Validation (local)

- Backend pytest: **172 passed**
- Frontend vitest: **28 passed**
- `tsc --noEmit` / Vite: see latest CI/local run

Live MTF after-deploy timings must be captured on production (not fabricated here).
