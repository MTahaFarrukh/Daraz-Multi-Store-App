# Phase 3 Hotfix — Real-Time RTS Reliability

**Date:** 2026-09-16  
**Scope:** Shipping Load RTS correctness + performance. Product Hub / print-safety architecture untouched.

Evidence: `data/phase3_hotfix_rts_diag.json`

---

## Root cause

**Shipping “Load RTS” was reading the local `daraz_orders` warehouse (`GET /api/orders?status_group=ready_to_ship`), not Daraz’s current ready_to_ship set.**

That caused:

1. **Missing today’s MTF RTS** — if Sync Orders was slow, failed, incomplete, or had not yet upserted those rows, Load RTS never saw them.
2. **“Yesterday’s orders” appearance** — local rows still tagged `status_group=ready_to_ship` (stale membership) could remain visible even when the live RTS set had moved on / differed.
3. **Unacceptably long Sync** — operators were forced through Sync (30-day `status=all` + per-order item hydration) just to print.

Print-event / UNPRINTED logic was **not** the bug and remains authoritative.

---

## MTF findings

Live authenticated probe against `mtfdigitalemporiumofficial_gmail_com`:

| Probe | Result |
|-------|--------|
| RTS **no date** | **FAIL E018** — CreatedAfter or UpdatedAfter mandatory |
| RTS `created_after` 30d / 90d / 180d | **17** orders, `countTotal=17` |
| RTS `update_after` 7d / 30d | **17** orders |

Live timestamps (marketplace +0800):

- `min_created_at`: **2026-09-16 00:46:20 +0800**
- `max_created_at`: **2026-09-16 23:30:25 +0800**

**Daraz does return today’s MTF RTS.** The gap was in MultiStore’s local-cache Load path, not seller credentials for this probe.

(Only one store was present in the local token vault for this diagnostic; production multi-store comparison should be re-run in the deployed workspace.)

---

## Yesterday-order finding

Orders created yesterday can **legitimately** still be RTS today. Shipping must ask for **current ready_to_ship**, not “created today.”

What looked like “stale yesterday data” in production was primarily **local warehouse membership**, not Daraz inventing old RTS. After this hotfix, a local stale RTS row that is **not** in the live response **does not appear**.

---

## Before architecture

```
Select stores → Sync Orders (30d all + items) → wait
→ Load RTS → GET /api/orders local status_group=ready_to_ship
→ print-state from order_label_prints
```

## After architecture

```
Select stores → Load RTS
→ POST /api/shipping/rts
→ bounded concurrent live /orders/get status=ready_to_ship
→ paginate + dedupe
→ reconcile order_label_prints
→ optional header upsert (side effect for print UUIDs)
→ UI with per-store ✓/✕
```

Local warehouse remains for Orders browsing/history. It is **not** Shipping membership SoT.

---

## Live RTS request

Exact safe parameters now used:

| Param | Value |
|-------|--------|
| endpoint | `GET /orders/get` |
| `status` | `ready_to_ship` |
| `created_after` | now − **180 days** (wide; not “today”) |
| `update_after` | not sent |
| `limit` | 50 |
| `offset` | paginated |
| `sort_by` | `updated_at` |
| `sort_direction` | `DESC` |

Fresh `DarazClient` per store via `client_for_store(store)` — no shared mutable token object across stores.

---

## Date/timezone behavior

- Daraz rejects missing date window (**E018**).
- Marketplace timestamps observed as **+0800**.
- Wide `created_after` avoids missing older-created orders that became RTS today.
- Shipping does **not** filter “created today.”

---

## Pagination behavior

- Page size 50, dedupe by `order_id`, overlap detection, max offset guard.
- If `countTotal` > unique returned → **incomplete / not ok** for that store (no silent truncate success).

---

## Store credential isolation

- Resolve store by workspace slug/UUID each time.
- Refresh token only for that store when near expiry.
- New client per store in concurrent workers.
- Regression test asserts both store ids are used for client construction.

---

## Performance profile

| Path | Measurement |
|------|-------------|
| **Before (Shipping)** | Required Sync (often minutes with item hydration) + local list |
| **After Load RTS (MTF live)** | **~2.6s** for 17 RTS (single page) |
| **Concurrency** | `STORE_CONCURRENCY=3` for multi-store Load RTS |
| **Sync Orders** | Stores now sync with concurrency **2**; bottleneck remains **item hydration** (`include_items=True`, batched `/orders/items/get` per page of headers) |

Sync is no longer on the critical path for printing.

---

## Sync Orders profile

Bottleneck identity:

1. `status=all` over ~30 days → large page volume  
2. **Per-page item hydration** after headers (`_fetch_items_for_orders`)  
3. Previously sequential per-store (now bounded concurrent)

Recommendation: keep warehouse sync for Orders UI; use header-only / narrower windows later if needed — **not** required for Shipping after this hotfix.

---

## Shipping behavior

- Primary action: **Load RTS** (live).
- Removed Sync-first requirement from Shipping UX.
- Per-store panel: ✓ count / ✕ Failed + Retry.
- Partial batch sets error banner while keeping successful stores’ rows.

---

## Print-state reconciliation

Unchanged semantics from `order_label_prints`:

| Events | Badge |
|--------|--------|
| 0 | UNPRINTED |
| 1 | Printed \<time\> |
| >1 | Reprinted N× |

Live RTS rows reconcile via `count_label_prints(store_uuid, daraz_order_id)` (+ summary after header upsert).

---

## Partial failure UX

Example:

```
✓ Taha — 18 RTS
✓ Sikander — 12 RTS
✕ MTF — Failed … [Retry]
```

Plus: `PARTIAL — some stores failed` — never presented as full success.

---

## Tests

| Suite | Result |
|-------|--------|
| Hotfix `tests/test_shipping_rts_hotfix.py` | included |
| Full backend `pytest` | **127 passed** |
| Frontend vitest | **19 passed** |

Covered: no sync required, stale local excluded, live missing-local included, print reconcile, partial failure, empty≠all, countTotal incomplete, older-created included, credential isolation, API endpoint.

---

## Production build

Frontend `tsc` + `vite build`: run after TS fix for Set selection — verify green in CI/local after this report’s companion build.

---

## Manual live verification

| Store (local vault) | Daraz countTotal | MultiStore live returned | Elapsed |
|---------------------|------------------|--------------------------|---------|
| MTF (`mtfdigitalemporiumofficial…`) | **17** | **17** | **~2556 ms** |

Created range: **2026-09-16** (today +0800) — matches “today’s RTS exist on Daraz.”

**Re-run on production** with all three connected stores (TEST A–E in the brief) after deploy — local vault only had MTF.

---

## Remaining risks

1. Production must re-verify Taha / Sikander / MTF side-by-side after deploy.  
2. Extremely old RTS (created >180d) could still be truncated by the required date window — monitor; widen if Seller Center shows gaps.  
3. Header upsert is sync-on-load (lightweight); item packages for print still resolve at print time via existing print path.  
4. Sync Orders still slow when used for warehouse — expected until optional header-only mode.

---

## Recommendation

**Shipping is production-safer for RTS membership after this hotfix**, provided production multi-store live checks pass post-deploy.

**Do not** treat Sync Orders duration as a Shipping blocker anymore.  
**Do** run the three-store Seller Center count comparison once on the deployed app.

**STOP.** No further phase started.
