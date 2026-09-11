# Phase 2.5A — Daraz Store Performance Capability Report

**Date:** 2026-09-11  
**Scope:** Research / API validation only (no dashboard, no migrations, no scheduler)  
**Live store tested (READ-ONLY):** local vault store `mtfdigitalemporiumofficial_gmail_com` (display: Mtfdigitalemporiumofficial)

---

## Executive conclusion

**PARTIALLY — YES for a monthly ranked Orders leaderboard; PARTIAL for Revenue.**

- **Orders ranking (month):** Feasible via `/orders/get` with `status=all`, `created_after` + `created_before`, using `data.countTotal` (verified live).
- **Direct Business Advisor / analytics aggregate API:** **Not available** on this app (`InvalidApiPath` for speculative BA/stats paths).
- **Revenue:** Order-level `price` exists but is **not** net seller income. Finance APIs **do work** (`/finance/transaction/details/get`, `/finance/payout/status/get`) and expose payout / fee / item_revenue style fields — suitable for a carefully labeled money metric after Phase 2.5B design.
- **Pagination:** `countTotal` is usable; **page `limit` appeared ignored** (requested 5, received 100); **offset behavior needs re-validation** before building full historical scrapers. Prefer `countTotal` for Orders metric when possible.

---

## Direct Business Advisor / analytics API

| Probe | Result | Confidence |
|-------|--------|------------|
| `/business/advisor/get` | `InvalidApiPath` | High — path not accepted by `api.daraz.pk` |
| `/data/order/statistics/get` | `InvalidApiPath` | High |
| Seller Center BA UI | Displays analytics in UI | Does **not** imply Open Platform access |

**Conclusion:** No verified direct aggregate Business Advisor Open Platform API for our app. Metrics must be **derived** from Orders and/or Finance.

---

## Orders capability

**Endpoint:** `GET /orders/get` (already implemented; extended with optional `created_before`, `update_after`, `update_before`)

| Parameter | Live result |
|-----------|-------------|
| `created_after` | Required (or `update_after`); works |
| `created_before` | Works (month window) |
| `update_after` alone | Works |
| `status=all` | Works |
| `status=canceled` | Works |
| `status=returned` | Works (0 in test month) |
| `limit` | Docs max 100; **observed: request limit=5 still returned 100 rows** |
| `offset` | Docs max 5000; **offset=10 probe showed 100% ID overlap with offset=0 — treat as unreliable until retested** |
| `sort_by` / `sort_direction` | Accepted |
| `countTotal` | Present and useful |
| `count` | Present (often equals page size / 100 in probes) |

**Live countTotals (sanitized):**

| Window | status | countTotal |
|--------|--------|------------|
| Last 7d | all | 217 |
| Current month (Sep 2026 bounds) | all | 302 |
| Current month | canceled | 100 |
| Current month | returned | 0 |
| Previous month (Aug) | all | 844 |
| Last ~90d | all | 2270 |

---

## Canonical Orders definition

**Recommended:**

```text
orders_count = data.countTotal from /orders/get
  where status = "all"
    and created_after = month_start
    and created_before = month_end
```

This counts **Daraz orders** (`order_id`), not order-items.

**Do not** use `items_count` sum or order-item rows as “Orders”.

**Caveat:** Status filter `all` includes canceled/unpaid/etc. Product may later offer a toggle for “completed/delivered only” via additional filtered queries — not required for v1 leaderboard if labeled “All orders”.

---

## Units Sold definition

**Recommended (Phase 2.5B+):**

```text
units_sold = SUM(order.items_count) over orders in period
  OR COUNT(order_item_id) from /order/items/get|/orders/items/get for non-canceled items
```

`items_count` is available on `/orders/get` rows (verified). Item-level accuracy needs item APIs when statuses mix canceled/delivered on the same order.

---

## Monetary fields discovered

### Order header (`/orders/get`, `/order/get`) — verified live

- `price` (string/number) — order total-like amount; Lazada docs: **not final transaction price; excludes voucher and shipping_fee**
- `shipping_fee`, `shipping_fee_original`, `shipping_fee_discount_platform`, `shipping_fee_discount_seller`
- `voucher`, `voucher_platform`, `voucher_seller`
- `cash_payment_fee`
- `payment_method`, `items_count`, `statuses[]`, `created_at`, `updated_at`

### Order items (`/order/items/get`) — verified live

- `item_price`, `paid_price` (paid ≈ item − voucher)
- `voucher_amount`, `voucher_platform`, `voucher_seller`, platform/seller LPI fields
- `shipping_amount`, shipping fee discount fields
- `tax_amount`, `currency`
- `order_item_id`, `status`, `package_id`, …

### Finance transaction details — verified live

Fields include: `amount`, `fee_name`, `fee_type`, `transaction_type`, `transaction_date`, `order_no`, `orderItem_no`, `orderItem_status`, `paid_status`, `VAT_in_amount`, `WHT_amount`, `statement`, `reference`, …

### Finance payout status — verified live

Fields include: `item_revenue`, `payout`, `opening_balance`, `closing_balance`, `fees_total`, `fees_on_refunds_total`, `refunds`, `shipment_fee`, `shipment_fee_credit`, `other_revenue_total`, `guarantee_deposit`, `paid`, `statement_number`, `created_at`, `updated_at`

---

## Recommended sales/revenue terminology

| UI label | Source idea | Notes |
|----------|-------------|-------|
| **Orders** | `countTotal` | Clear |
| **Gross Merchandise (order price)** | sum of order `price` | Customer-facing product total; **not** net seller income |
| **Customer paid (items)** | sum of item `paid_price` | Closer to paid goods value |
| **Item revenue (statement)** | payout `item_revenue` | Finance statement semantics |
| **Net payout** | payout `payout` / closing balances | Settled money; lag vs order month |

**Do not** label order `price` sum as “Revenue” or “Net Revenue” in the UI.

**v1 recommendation:** Rank by **Orders**; show optional **Gross Sales (order price)** with explicit subtitle; add **Net Payout** later from finance once month↔statement mapping is designed.

---

## Finance capability

| Endpoint | Live | Notes |
|----------|------|-------|
| `/finance/transaction/details/get` | **YES** (`code=0`) | Date params accepted as `YYYY-MM-DD` and ISO datetime |
| `/finance/payout/status/get` | **YES** (`code=0`) | `created_after=YYYY-MM-DD` returned rows; ISO month_start returned empty in one probe |

Join keys observed: `order_no`, `orderItem_no` on transactions → can relate to Daraz order/item IDs (string/number normalization required).

---

## Cancellation / Return / Refund capability

| Concept | Source | Live |
|---------|--------|------|
| Canceled orders | `/orders/get?status=canceled` | YES (`countTotal=100` in test month) |
| Returned orders | `/orders/get?status=returned` | YES endpoint; 0 in test month |
| Other reverse logistics statuses | order `statuses` e.g. `shipped_back`, `shipped_back_success` | Observed on `status=all` samples |
| Refund amounts | Finance `refunds`, `fees_on_refunds_total`, transaction types | YES on payout/transactions |
| Item cancel vs order cancel | Item `status` + `cancel_return_initiator` | Present on items |

Do not conflate canceled ≠ returned ≠ refund payout.

---

## Metric feasibility matrix

| Metric | Direct BA API | Orders | Finance | Reliability | Recommended source |
|--------|---------------|--------|---------|-------------|--------------------|
| Orders | No | Yes (`countTotal`) | No | High | Orders API |
| Units | No | Partial (`items_count`) | No | Medium | Orders (+ items for precision) |
| Gross Sales | No | Yes (`price` sum) | Partial | Medium | Orders `price` (labeled carefully) |
| AOV | No | Derived | No | Medium | Gross Sales / Orders |
| Net Revenue | No | No | Partial | Medium | Finance payout/`item_revenue` after design |
| Cancelled | No | Yes | Partial | High | Orders `status=canceled` |
| Returns | No | Yes (status) | Partial | Medium | Orders + finance refunds |
| Refunds (money) | No | No | Yes | Medium | Finance |
| Payout | No | No | Yes | High | `/finance/payout/status/get` |
| MoM Growth | No | Yes | Yes | High | Compare monthly aggregates |

---

## Real API test results

Sanitized summary (full dump: `data/spike_2_5a_results.json`, gitignored under `data/`).

| Probe | Result |
|-------|--------|
| `/orders/get` windows | `code=0`; fields include `price`, fees, vouchers, `statuses`, timestamps |
| `/order/get` | `code=0` |
| `/order/items/get` | `code=0`; `item_price`/`paid_price` present |
| `/seller/get` | `code=0` (name, seller_id, status, …) |
| `/finance/transaction/details/get` | `code=0` |
| `/finance/payout/status/get` | `code=0` |
| `/business/advisor/get` | InvalidApiPath |
| `/data/order/statistics/get` | InvalidApiPath |
| `/shipment/providers/get` | InvalidApiPath (this app) |

No tokens/secrets printed.

---

## Date/time findings

- Live order timestamps returned with **`+0800` offset** (Lazada-style), even for PK seller samples.
- Month bounds using `+05:00` still returned coherent month `countTotal`s, but **Phase 2.5B should normalize on API timestamp semantics** (likely treat marketplace timestamps as `+08:00` unless Daraz documents otherwise).
- Recommend storing all events in UTC internally; define “calendar month” in an explicit zone constant (document choice).

---

## Pagination and rate limits

| Topic | Finding |
|-------|---------|
| Max page size (docs) | 100 |
| Observed page size | Often 100 even when lower `limit` requested |
| Offset (docs) | max 5000 |
| Offset (live) | **Unreliable in spike** — needs dedicated retest |
| `countTotal` | Reliable enough for Orders metric without full scan |
| Rate limits | Not quantitatively measured; no client retry yet |
| Call volume estimate (if forced full scan @100/page) | 10×1000 ≈ 100 calls/month; 50×5000 ≈ 2500 calls/month — plus items/finance |

**Architecture implication:** Prefer `countTotal` + sampled/aggregated finance over full order scrapes for the leaderboard.

---

## Historical availability

Verified live for this store:

- Current month
- Previous month
- ~90 days (`countTotal=2270`)

**Not proven:** 6–12 months retention. Retest with older `created_after` in 2.5B before promising year-long history.

---

## Recommended sync frequencies

| Data | Suggestion |
|------|------------|
| Current-month Orders `countTotal` | Every 15–30 minutes per store (cheap: 1 call) |
| Closed prior months | Once/day or on-demand reconcile |
| Finance payout/transactions | Every 6–24 hours (settlement lag) |
| Full order body ingest | Align with Phase 3 Unified Orders — not for leaderboard alone |

---

## Recommended Phase 2.5B architecture

**Hybrid (Option C), biased to aggregates for performance + raw for Phase 3:**

1. **Performance path:** sync daily/monthly **aggregate facts** (`orders_count`, optional gross_sales, cancel_count) from Orders `countTotal` / bounded scans; finance snapshots for payout metrics.
2. **Orders path (Phase 3):** persist normalized orders/items when Unified Orders lands — then recompute metrics from local data.
3. Avoid building a throwaway full-order warehouse only for the leaderboard.

---

## Proposed DB schema (PROPOSAL ONLY — do not migrate yet)

```text
store_metrics_daily (
  id UUID PK,
  workspace_id UUID NOT NULL,
  store_uuid UUID NOT NULL REFERENCES daraz_stores(id),  -- internal PK
  metric_date DATE NOT NULL,  -- date in chosen marketplace timezone
  orders_count INT,
  units_sold INT NULL,
  gross_sales_order_price NUMERIC NULL,  -- sum(order.price); nullable until enabled
  cancelled_orders_count INT NULL,
  returned_orders_count INT NULL,
  currency TEXT NULL,
  source TEXT NOT NULL,  -- orders_api | finance_api | derived
  last_synced_at TIMESTAMPTZ NOT NULL,
  UNIQUE (store_uuid, metric_date, source)
)

-- optional later:
store_finance_snapshots (
  store_uuid UUID REFERENCES daraz_stores(id),
  statement_number TEXT,
  item_revenue NUMERIC,
  payout NUMERIC,
  fees_total NUMERIC,
  refunds NUMERIC,
  paid BOOLEAN,
  created_at TIMESTAMPTZ,
  UNIQUE (store_uuid, statement_number)
)
```

---

## Future leaderboard rules

Default period: current calendar month (zone TBD).  
Default rank: `orders_count` DESC.  
Tie-break: gross_sales_order_price DESC → `daraz_stores.id` ASC.  

Revenue mode: selected money metric DESC → orders_count DESC → store UUID ASC.

---

## Missing permissions / blockers

| Item | Classification |
|------|----------------|
| Business Advisor aggregate API | **Endpoint does not exist / not exposed** (`InvalidApiPath`) |
| `/shipment/providers/get` | **Exists in our client but InvalidApiPath** — path or app permission issue |
| Order `limit`/`offset` quirks | **Endpoint exists; behavior inconsistent** — engineering risk |
| Net revenue = order month | **Conceptual gap** — payout statements ≠ order create month |
| 12-month history | **Not verified** |

---

## Files created/modified during spike

| File | Change |
|------|--------|
| `src/daraz_api.py` | Optional order date params; finance GET helpers |
| `scripts/spike_store_performance.py` | READ-ONLY capability probe |
| `scripts/_summarize_spike_2_5a.py` | Local summary helper |
| `tests/test_daraz_performance_spike.py` | Unit tests for helpers |
| `docs/PHASE2_5A_CAPABILITY.md` | This report |
| `data/spike_2_5a_results.json` | Live sanitized results (local `data/`, not for commit of secrets) |

---

## Tests

```text
pytest tests/test_daraz_performance_spike.py
```

(plus existing suite still green when run)

---

## Final recommendation — what Phase 2.5B should implement

1. **Monthly Orders leaderboard** using `/orders/get` `countTotal` + month bounds; join on `daraz_stores.id`.
2. **Explicit money labeling** if showing order `price` sums (Gross Sales — not Net Revenue).
3. **Finance sync spike → design** for Net Payout / Item Revenue (statement-based), separate from order-month gross.
4. **Retest pagination** before any full-history backfill job.
5. **Timezone decision** documented (+08 API timestamps observed).
6. **Do not** build BA integration or speculative analytics endpoints.
7. **Align** aggregate store with upcoming Phase 3 order persistence (hybrid).

**STOP — do not start Phase 2.5B until review.**
