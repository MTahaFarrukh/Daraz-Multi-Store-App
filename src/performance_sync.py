"""Store performance sync + ranking (Phase 2.5B).

Orders metric uses /orders/get countTotal (Phase 2.5A validated).
Gross Sales sums order.price across paginated pages (page-sized offsets).
Finance / net payout is intentionally excluded from this layer.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from src.daraz_api import DarazApiError, DarazClient
from src.db.repo import get_repo
from src.ops import client_for_store
from src.performance_time import (
    MARKETPLACE_TZ_LABEL,
    current_marketplace_month,
    growth_pct,
    month_window_iso,
    previous_month,
)
from src.token_refresh import refresh_one_store
from src.token_store import access_token_expires_soon

logger = logging.getLogger(__name__)

GROSS_SALES_ENABLED = True
PAGE_SIZE = 100
MAX_OFFSET = 5000  # Daraz documented max
SOURCE_ORDERS_API = "orders_api"


def parse_money(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def fetch_orders_count_total(
    client: DarazClient,
    *,
    year: int,
    month: int,
) -> int:
    created_after, created_before = month_window_iso(year, month)
    resp = client.get_orders(
        created_after=created_after,
        created_before=created_before,
        status="all",
        limit=1,
        offset=0,
        sort_by="created_at",
        sort_direction="ASC",
    )
    data = resp.get("data") or {}
    total = data.get("countTotal")
    if total is None:
        total = data.get("count") or 0
    return int(total)


def fetch_gross_sales_order_price(
    client: DarazClient,
    *,
    year: int,
    month: int,
    expected_total: int | None = None,
) -> tuple[Decimal, str | None]:
    """Sum order.price across pages. Returns (sum, warning_or_None)."""
    if not GROSS_SALES_ENABLED:
        return Decimal("0"), "gross_sales_disabled"

    created_after, created_before = month_window_iso(year, month)
    total = Decimal("0")
    seen: set[str] = set()
    offset = 0
    pages = 0
    truncated = False

    while offset <= MAX_OFFSET:
        resp = client.get_orders(
            created_after=created_after,
            created_before=created_before,
            status="all",
            limit=PAGE_SIZE,
            offset=offset,
            sort_by="created_at",
            sort_direction="ASC",
        )
        orders = (resp.get("data") or {}).get("orders") or []
        if not orders:
            break
        pages += 1
        page_ids = []
        for order in orders:
            oid = str(order.get("order_id") or "")
            page_ids.append(oid)
            if oid and oid in seen:
                # Overlap → stop; do not trust incomplete sum
                return total, "pagination_overlap_detected"
            if oid:
                seen.add(oid)
            total += parse_money(order.get("price"))
        if len(orders) < PAGE_SIZE:
            break
        # Detect non-advancing pages
        if len(set(page_ids)) < len(page_ids):
            return total, "duplicate_ids_in_page"
        offset += PAGE_SIZE
        if expected_total is not None and len(seen) >= expected_total:
            break
        if offset > MAX_OFFSET:
            truncated = True
            break

    warn = None
    if truncated or (expected_total is not None and len(seen) < expected_total):
        warn = "pagination_truncated_offset_limit"
    return total, warn


def compute_growth_fields(
    current_orders: int,
    previous_orders: int | None,
    current_gross: Decimal | None,
    previous_gross: Decimal | None,
) -> dict[str, Any]:
    return {
        "previous_orders_count": previous_orders,
        "orders_growth_pct": growth_pct(current_orders, previous_orders),
        "previous_gross_sales": (
            float(previous_gross) if previous_gross is not None else None
        ),
        "gross_sales_growth_pct": growth_pct(
            float(current_gross) if current_gross is not None else None,
            float(previous_gross) if previous_gross is not None else None,
        ),
    }


def sync_store_month(
    *,
    workspace_id: str,
    store: dict[str, Any],
    year: int,
    month: int,
    include_gross_sales: bool = True,
) -> dict[str, Any]:
    """Sync one store/month. Never raises for Daraz failures — returns status row."""
    repo = get_repo()
    store_uuid = str(store.get("id") or "")
    slug = str(store.get("store_id") or "")
    if not store_uuid:
        return {
            "store_uuid": None,
            "store_id": slug,
            "sync_status": "error",
            "sync_error": "missing_internal_store_id",
        }

    try:
        # Best-effort token refresh; ignore soft failures
        try:
            def _upsert(record: dict[str, Any]) -> dict[str, Any]:
                return repo.upsert_store(workspace_id, record)

            if access_token_expires_soon(store, within_minutes=60):
                refresh_one_store(store, upsert_fn=_upsert)
            refreshed = repo.get_store(workspace_id, slug) or store
        except Exception as exc:  # noqa: BLE001
            logger.info("Token refresh skipped for %s: %s", slug, exc)
            refreshed = store

        client = client_for_store(refreshed)
        client.timeout = 60.0
        orders_count = fetch_orders_count_total(client, year=year, month=month)

        gross: Decimal | None = None
        gross_warn: str | None = None
        if include_gross_sales and GROSS_SALES_ENABLED:
            gross, gross_warn = fetch_gross_sales_order_price(
                client, year=year, month=month, expected_total=orders_count
            )
            if gross_warn == "pagination_overlap_detected":
                gross = None

        py, pm = previous_month(year, month)
        prev = repo.get_store_performance(workspace_id, store_uuid, py, pm)
        prev_orders = int(prev["orders_count"]) if prev else None
        prev_gross = (
            Decimal(str(prev["gross_sales"]))
            if prev and prev.get("gross_sales") is not None
            else None
        )
        # If previous month missing, try a light count-only fetch for MoM
        if prev_orders is None:
            try:
                prev_orders = fetch_orders_count_total(client, year=py, month=pm)
            except DarazApiError:
                prev_orders = None

        growth = compute_growth_fields(orders_count, prev_orders, gross, prev_gross)
        status = "ok"
        err = None
        if gross_warn and gross is None:
            status = "partial"
            err = gross_warn
        elif gross_warn:
            status = "partial"
            err = gross_warn

        row = repo.upsert_store_performance(
            workspace_id=workspace_id,
            store_uuid=store_uuid,
            year=year,
            month=month,
            orders_count=orders_count,
            gross_sales=float(gross) if gross is not None else None,
            currency="PKR",
            source=SOURCE_ORDERS_API,
            sync_status=status,
            sync_error=err,
            orders_synced=True,
            gross_synced=gross is not None,
            **growth,
        )
        return row
    except DarazApiError as exc:
        logger.warning("Perf sync Daraz error store=%s: %s", slug, exc)
        err = f"daraz:{exc.code}:{str(exc)[:160]}"
        existing = repo.get_store_performance(workspace_id, store_uuid, year, month)
        if existing:
            return repo.upsert_store_performance(
                workspace_id=workspace_id,
                store_uuid=store_uuid,
                year=year,
                month=month,
                sync_status="error",
                sync_error=err,
                preserve_counts_on_error=True,
            )
        return {
            "store_id": store_uuid,
            "workspace_id": workspace_id,
            "year": year,
            "month": month,
            "orders_count": 0,
            "gross_sales": None,
            "sync_status": "error",
            "sync_error": err,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Perf sync failed store=%s", slug)
        return {
            "store_id": store_uuid,
            "workspace_id": workspace_id,
            "year": year,
            "month": month,
            "sync_status": "error",
            "sync_error": f"{type(exc).__name__}:{str(exc)[:160]}",
        }


def sync_workspace_month(
    workspace_id: str,
    *,
    year: int | None = None,
    month: int | None = None,
    include_gross_sales: bool = True,
) -> dict[str, Any]:
    if year is None or month is None:
        year, month = current_marketplace_month()
    repo = get_repo()
    stores = repo.list_stores(workspace_id)
    results = []
    for store in stores:
        results.append(
            sync_store_month(
                workspace_id=workspace_id,
                store=store,
                year=year,
                month=month,
                include_gross_sales=include_gross_sales,
            )
        )
    return {
        "year": year,
        "month": month,
        "timezone": MARKETPLACE_TZ_LABEL,
        "gross_sales_enabled": GROSS_SALES_ENABLED and include_gross_sales,
        "stores_total": len(stores),
        "stores_ok": sum(1 for r in results if r.get("sync_status") == "ok"),
        "stores_partial": sum(1 for r in results if r.get("sync_status") == "partial"),
        "stores_error": sum(1 for r in results if r.get("sync_status") == "error"),
        "results": results,
    }


def rank_performance_rows(
    rows: list[dict[str, Any]],
    *,
    metric: str = "orders",
) -> list[dict[str, Any]]:
    metric = (metric or "orders").lower()
    if metric not in {"orders", "gross_sales"}:
        metric = "orders"

    def sort_key(row: dict[str, Any]):
        orders = int(row.get("orders_count") or 0)
        gross = row.get("gross_sales")
        gross_f = float(gross) if gross is not None else -1.0
        sid = str(row.get("store_id") or "")
        if metric == "gross_sales":
            return (-gross_f, -orders, sid)
        return (-orders, -gross_f, sid)

    ranked = sorted(rows, key=sort_key)
    out = []
    for i, row in enumerate(ranked, start=1):
        item = dict(row)
        item["rank"] = i
        out.append(item)
    return out


def leaderboard_safe_view(row: dict[str, Any], store: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fields safe for workspace UI and future community leaderboards."""
    display = ""
    if store:
        display = (
            store.get("display_name")
            or store.get("shop_name")
            or store.get("store_name")
            or store.get("store_id")
            or ""
        )
    return {
        # Leaderboard-safe: UUID + display metrics only (no workspace/tokens/account).
        "store_id": row.get("store_id"),
        "display_name": display,
        "rank": row.get("rank"),
        "orders_count": int(row.get("orders_count") or 0),
        "gross_sales": (
            float(row["gross_sales"]) if row.get("gross_sales") is not None else None
        ),
        "currency": row.get("currency") or "PKR",
        "orders_growth_pct": row.get("orders_growth_pct"),
        "gross_sales_growth_pct": row.get("gross_sales_growth_pct"),
        "year": row.get("year"),
        "month": row.get("month"),
        "sync_status": row.get("sync_status"),
        "orders_synced_at": row.get("orders_synced_at"),
        "gross_sales_synced_at": row.get("gross_sales_synced_at"),
        "updated_at": row.get("updated_at"),
    }
