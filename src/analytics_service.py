"""Workspace analytics from local order data (no live Daraz fetches)."""

from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from src.db.repo import get_repo


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last = monthrange(year, month)[1]
    start = f"{year:04d}-{month:02d}-01T00:00:00"
    end = f"{year:04d}-{month:02d}-{last:02d}T23:59:59"
    return start, end


def _prev_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _parse_day(value: Any) -> str | None:
    if not value:
        return None
    s = str(value)
    if len(s) >= 10:
        return s[:10]
    return None


def _mom_pct(current: float, previous: float | None) -> float | None:
    if previous is None:
        return None
    if previous == 0:
        return None if current == 0 else None  # N/A — avoid infinite %
    return round(((current - previous) / previous) * 100.0, 2)


def _orders_in_window(
    workspace_id: str,
    *,
    date_from: str,
    date_to: str,
    store_id: str | None = None,
) -> list[dict[str, Any]]:
    repo = get_repo()
    stores = {str(s["id"]): s for s in repo.list_stores(workspace_id)}
    slug_map = {str(s.get("store_id") or "").lower(): str(s["id"]) for s in stores.values()}
    store_uuid = None
    if store_id:
        store_uuid = store_id if store_id in stores else slug_map.get(store_id.lower())

    # Pull a large page — analytics is aggregate over local cache.
    result = repo.list_orders(
        workspace_id,
        {
            "date_from": date_from,
            "date_to": date_to,
            "store_uuids": [store_uuid] if store_uuid else None,
            "page": 1,
            "page_size": 200,
            "sort": "created_at_daraz_asc",
        },
    )
    items = list(result.get("items") or [])
    # If truncated, keep fetching pages for memory/postgres that support page.
    total = int(result.get("total") or len(items))
    page = 2
    while len(items) < total and page <= 50:
        more = repo.list_orders(
            workspace_id,
            {
                "date_from": date_from,
                "date_to": date_to,
                "store_uuids": [store_uuid] if store_uuid else None,
                "page": page,
                "page_size": 200,
                "sort": "created_at_daraz_asc",
            },
        )
        batch = more.get("items") or []
        if not batch:
            break
        items.extend(batch)
        page += 1
    return items


def analytics_dashboard(
    workspace_id: str,
    *,
    year: int | None = None,
    month: int | None = None,
    store_id: str | None = None,
) -> dict[str, Any]:
    today = date.today()
    year = int(year or today.year)
    month = int(month or today.month)
    start, end = _month_bounds(year, month)
    py, pm = _prev_month(year, month)
    p_start, p_end = _month_bounds(py, pm)

    current = _orders_in_window(
        workspace_id, date_from=start, date_to=end, store_id=store_id
    )
    previous = _orders_in_window(
        workspace_id, date_from=p_start, date_to=p_end, store_id=store_id
    )

    def _gross(rows: list[dict[str, Any]]) -> float:
        total = 0.0
        for o in rows:
            try:
                total += float(o.get("price") or 0)
            except (TypeError, ValueError):
                continue
        return round(total, 2)

    orders_n = len(current)
    gross = _gross(current)
    prev_orders = len(previous)
    prev_gross = _gross(previous)
    aov = round(gross / orders_n, 2) if orders_n else None

    # Daily series
    last_day = monthrange(year, month)[1]
    by_day_orders: dict[str, int] = defaultdict(int)
    by_day_sales: dict[str, float] = defaultdict(float)
    for o in current:
        day = _parse_day(o.get("created_at_daraz") or o.get("created_at"))
        if not day:
            continue
        by_day_orders[day] += 1
        try:
            by_day_sales[day] += float(o.get("price") or 0)
        except (TypeError, ValueError):
            pass

    daily_orders = []
    daily_sales = []
    for d in range(1, last_day + 1):
        key = f"{year:04d}-{month:02d}-{d:02d}"
        daily_orders.append({"date": key, "orders": by_day_orders.get(key, 0)})
        daily_sales.append(
            {"date": key, "gross_sales": round(by_day_sales.get(key, 0.0), 2)}
        )

    status_dist: dict[str, int] = defaultdict(int)
    for o in current:
        status_dist[str(o.get("status_group") or "other")] += 1

    repo = get_repo()
    stores = {str(s["id"]): s for s in repo.list_stores(workspace_id)}
    by_store: dict[str, dict[str, Any]] = {}
    for o in current:
        sid = str(o.get("store_id"))
        st = stores.get(sid) or {}
        bucket = by_store.setdefault(
            sid,
            {
                "store_uuid": sid,
                "store_id": st.get("store_id"),
                "store_display_name": st.get("display_name")
                or st.get("store_name")
                or st.get("store_id")
                or sid[:8],
                "orders": 0,
                "gross_sales": 0.0,
            },
        )
        bucket["orders"] += 1
        try:
            bucket["gross_sales"] += float(o.get("price") or 0)
        except (TypeError, ValueError):
            pass
    store_comparison = sorted(
        [
            {**v, "gross_sales": round(float(v["gross_sales"]), 2)}
            for v in by_store.values()
        ],
        key=lambda r: (-r["orders"], -r["gross_sales"], str(r.get("store_id") or "")),
    )

    mom_orders = _mom_pct(float(orders_n), float(prev_orders) if previous or prev_orders else None)
    # If previous month has zero orders and current has some → N/A (not infinite)
    if prev_orders == 0:
        mom_orders = None
    mom_sales = _mom_pct(gross, prev_gross if previous or prev_gross else None)
    if prev_gross == 0:
        mom_sales = None

    return {
        "month": f"{year:04d}-{month:02d}",
        "previous_month": f"{py:04d}-{pm:02d}",
        "label": "Gross Sales",
        "metric_notes": {
            "gross_sales": (
                "Sum of local order price fields for the selected month. "
                "Not profit, net revenue, or payout."
            )
        },
        "summary": {
            "orders": orders_n,
            "gross_sales": gross,
            "average_order_value": aov,
            "previous_orders": prev_orders,
            "previous_gross_sales": prev_gross,
            "mom_orders_pct": mom_orders,
            "mom_gross_sales_pct": mom_sales,
            "store_count": len(stores),
            "active_stores": len(by_store),
        },
        "orders_over_time": daily_orders,
        "gross_sales_over_time": daily_sales,
        "status_distribution": [
            {"status": k, "count": v}
            for k, v in sorted(status_dist.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "store_comparison": store_comparison,
        "top_stores_by_orders": store_comparison[:10],
    }
