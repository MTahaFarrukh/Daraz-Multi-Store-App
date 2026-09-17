"""Live Shipping RTS fetch — Daraz current ready_to_ship is source of truth.

Architecture
-----------
selected stores
  → bounded concurrent live ``/orders/get`` (status=ready_to_ship)
  → paginate + dedupe by order_id
  → reconcile ``order_label_prints`` (UNPRINTED / Printed / Reprinted)
  → optional lightweight header upsert into ``daraz_orders`` (side effect)
  → return Shipping list

Does NOT require a prior full ``/api/orders/sync``.
Does NOT use local status_group as the RTS membership set.

Date window
-----------
Daraz requires CreatedAfter OR UpdatedAfter (E018). Primary fetch uses
``update_after`` (default 90 days) — current RTS rows usually have a recent
pack/RTS update. If primary returns zero orders, expand once with
``created_after`` (default 180 days). Status=ready_to_ship is the current-RTS
filter — not "orders created today".
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

from src.daraz_api import DarazApiError
from src.db.repo import get_repo
from src.ops import client_for_store
from src.order_sync import order_payload_from_daraz
from src.orders import extract_orders
from src.store_display import store_display_name
from src.token_refresh import refresh_one_store
from src.token_store import access_token_expires_soon

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
MAX_OFFSET = 5000
STORE_CONCURRENCY = 3


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


# Primary: recent updates (packed / RTS'd). Fallback expand: wide created window.
RTS_UPDATE_AFTER_DAYS = _env_int("RTS_UPDATE_AFTER_DAYS", 90)
RTS_CREATED_AFTER_DAYS = _env_int("RTS_CREATED_AFTER_DAYS", 180)


def _iso_ago(days: int) -> str:
    return (
        datetime.now(UTC) - timedelta(days=days)
    ).astimezone().replace(microsecond=0).isoformat()


def _min_max(values: list[str]) -> tuple[str | None, str | None]:
    clean = [v for v in values if v]
    if not clean:
        return None, None
    return min(clean), max(clean)


def _paginate_orders_get(
    client: Any,
    *,
    update_after: str | None = None,
    created_after: str | None = None,
    token_refresh_triggered: bool = False,
    token_refresh_ms: float = 0.0,
    request_number_start: int = 1,
) -> dict[str, Any]:
    """Paginate ``/orders/get`` for ready_to_ship. Records per-page timings."""
    seen: set[str] = set()
    raw_orders: list[dict[str, Any]] = []
    offset = 0
    count_total: int | None = None
    warning: str | None = None
    pages = 0
    orders_get_ms = 0.0
    normalize_ms = 0.0
    request_timings: list[dict[str, Any]] = []
    request_number = request_number_start
    last_daraz_code = "0"

    while offset <= MAX_OFFSET:
        page_token_triggered = token_refresh_triggered and request_number == request_number_start
        page_token_ms = token_refresh_ms if page_token_triggered else 0.0

        pg0 = time.perf_counter()
        kwargs: dict[str, Any] = {
            "status": "ready_to_ship",
            "limit": PAGE_SIZE,
            "offset": offset,
            "sort_by": "updated_at",
            "sort_direction": "DESC",
        }
        if update_after:
            kwargs["update_after"] = update_after
        if created_after:
            kwargs["created_after"] = created_after
        resp = client.get_orders(**kwargs)
        api_ms = (time.perf_counter() - pg0) * 1000
        orders_get_ms += api_ms
        pages += 1

        daraz_code = str(resp.get("code") or "0")
        last_daraz_code = daraz_code
        data = resp.get("data") or {}
        ct = data.get("countTotal") or data.get("count_total") or data.get("total_count")
        page_count_total: int | None = None
        if ct is not None:
            try:
                page_count_total = int(ct)
                count_total = page_count_total
            except (TypeError, ValueError):
                pass

        n0 = time.perf_counter()
        orders = extract_orders(resp)
        page_normalize_ms = (time.perf_counter() - n0) * 1000
        normalize_ms += page_normalize_ms

        request_timings.append(
            {
                "request_number": request_number,
                "offset": offset,
                "limit": PAGE_SIZE,
                "returned_count": len(orders),
                "countTotal": page_count_total,
                "api_ms": round(api_ms, 1),
                "daraz_code": daraz_code,
                "normalize_ms": round(page_normalize_ms, 1),
                "token_refresh_triggered": page_token_triggered,
                "token_refresh_ms": round(page_token_ms, 1),
                "mode": "update_after" if update_after else "created_after",
            }
        )
        request_number += 1

        if not orders:
            break

        page_ids: list[str] = []
        overlap = False
        for order in orders:
            oid = str(order.get("order_id") or "")
            page_ids.append(oid)
            if oid and oid in seen:
                overlap = True
                break
            if oid:
                seen.add(oid)
                raw_orders.append(order)
        if overlap:
            warning = "pagination_overlap_detected"
            break
        if len(set(page_ids)) < len([x for x in page_ids if x]):
            warning = "duplicate_ids_in_page"
            break

        # Stop when this page holds everything or is a short page.
        if count_total is not None and count_total <= PAGE_SIZE:
            break
        if len(orders) < PAGE_SIZE:
            break
        if count_total is not None and (offset + len(orders)) >= count_total:
            break

        offset += PAGE_SIZE
        if offset > MAX_OFFSET:
            warning = "pagination_truncated_offset_limit"
            break

    incomplete = False
    if count_total is not None and count_total > len(seen):
        incomplete = True
        warning = warning or "countTotal_mismatch"

    return {
        "raw_orders": raw_orders,
        "seen": seen,
        "offset": offset,
        "pages": pages,
        "count_total": count_total,
        "warning": warning,
        "incomplete": incomplete,
        "orders_get_ms": orders_get_ms,
        "normalize_ms": normalize_ms,
        "request_timings": request_timings,
        "daraz_code": last_daraz_code,
        "next_request_number": request_number,
    }


def _batch_print_state(
    repo: Any,
    workspace_id: str,
    store_uuid: str,
    rows: list[tuple[str, str | None]],
) -> dict[str, dict[str, Any]]:
    """Batch print reconcile for (daraz_order_id, local_id) pairs."""
    local_ids = [lid for _, lid in rows if lid]
    by_local: dict[str, list[dict[str, Any]]] = {}
    if local_ids:
        by_local = repo.list_label_prints_for_orders(workspace_id, local_ids)

    out: dict[str, dict[str, Any]] = {}
    for daraz_oid, local_id in rows:
        if local_id:
            prints = by_local.get(str(local_id), [])
            last = prints[0] if prints else None
            out[daraz_oid] = {
                "print_count": len(prints),
                "last_printed_at": last.get("printed_at") if last else None,
            }
        else:
            pc = repo.count_label_prints(workspace_id, store_uuid, daraz_oid)
            out[daraz_oid] = {"print_count": pc, "last_printed_at": None}
    return out


def fetch_store_live_rts(
    workspace_id: str,
    store: dict[str, Any],
    *,
    created_after: str | None = None,
    update_after: str | None = None,
    upsert_headers: bool = True,
) -> dict[str, Any]:
    """Fetch current ready_to_ship for one store. Isolated credentials."""
    repo = get_repo()
    store_uuid = str(store.get("id") or "")
    slug = str(store.get("store_id") or "")
    display = store_display_name(store)
    account = str(store.get("account") or "")
    t0 = time.perf_counter()

    diag_base = {
        "store_uuid": store_uuid or None,
        "store_id": slug,
        "display_name": display,
        "account": account,
        "endpoint": "/orders/get",
        "status": "ready_to_ship",
        "limit": PAGE_SIZE,
    }

    if not store_uuid:
        return {
            **diag_base,
            "ok": False,
            "incomplete": True,
            "error": "missing_internal_store_id",
            "orders": [],
            "returned": 0,
            "unique": 0,
            "elapsed_ms": 0,
        }

    update_window = update_after or _iso_ago(RTS_UPDATE_AFTER_DAYS)
    created_window = created_after or _iso_ago(RTS_CREATED_AFTER_DAYS)
    token_refresh_ms = 0.0
    token_refresh_triggered = False
    orders_get_ms = 0.0
    normalize_ms = 0.0
    reconcile_ms = 0.0
    request_timings: list[dict[str, Any]] = []
    window_mode = "update_after"
    used_created_after: str | None = None
    used_update_after: str | None = update_window

    try:
        try:

            def _upsert(record: dict[str, Any]) -> dict[str, Any]:
                return repo.upsert_store(workspace_id, record)

            if access_token_expires_soon(store, within_minutes=60):
                token_refresh_triggered = True
                tr0 = time.perf_counter()
                refresh_one_store(store, upsert_fn=_upsert)
                token_refresh_ms = (time.perf_counter() - tr0) * 1000
            refreshed = repo.get_store(workspace_id, slug) or store
        except Exception as exc:  # noqa: BLE001
            logger.info("RTS token refresh skipped store=%s: %s", slug, exc)
            refreshed = store

        # Always build a fresh client from this store's token — never reuse across stores.
        client = client_for_store(refreshed)
        client.timeout = 45.0

        # Explicit created_after override (caller) skips update_after primary.
        if created_after and not update_after:
            window_mode = "created_after"
            used_update_after = None
            used_created_after = created_window
            page_result = _paginate_orders_get(
                client,
                created_after=created_window,
                token_refresh_triggered=token_refresh_triggered,
                token_refresh_ms=token_refresh_ms,
            )
            request_timings.extend(page_result["request_timings"])
            orders_get_ms += page_result["orders_get_ms"]
            normalize_ms += page_result["normalize_ms"]
            logger.info(
                "RTS store=%s mode=created_after days=%s returned=%s",
                slug,
                RTS_CREATED_AFTER_DAYS,
                len(page_result["raw_orders"]),
            )
        else:
            page_result = _paginate_orders_get(
                client,
                update_after=update_window,
                token_refresh_triggered=token_refresh_triggered,
                token_refresh_ms=token_refresh_ms,
            )
            request_timings.extend(page_result["request_timings"])
            orders_get_ms += page_result["orders_get_ms"]
            normalize_ms += page_result["normalize_ms"]

            if not page_result["raw_orders"]:
                window_mode = "created_after_expand"
                used_created_after = created_window
                used_update_after = update_window  # primary was attempted
                logger.info(
                    "RTS store=%s primary update_after empty; expanding created_after=%sd",
                    slug,
                    RTS_CREATED_AFTER_DAYS,
                )
                page_result = _paginate_orders_get(
                    client,
                    created_after=created_window,
                    token_refresh_triggered=False,
                    token_refresh_ms=0.0,
                    request_number_start=page_result["next_request_number"],
                )
                request_timings.extend(page_result["request_timings"])
                orders_get_ms += page_result["orders_get_ms"]
                normalize_ms += page_result["normalize_ms"]
                logger.info(
                    "RTS store=%s mode=created_after_expand days=%s returned=%s",
                    slug,
                    RTS_CREATED_AFTER_DAYS,
                    len(page_result["raw_orders"]),
                )
            else:
                logger.info(
                    "RTS store=%s mode=update_after days=%s returned=%s",
                    slug,
                    RTS_UPDATE_AFTER_DAYS,
                    len(page_result["raw_orders"]),
                )

        raw_orders: list[dict[str, Any]] = page_result["raw_orders"]
        seen: set[str] = page_result["seen"]
        offset = page_result["offset"]
        pages = len(request_timings)
        count_total = page_result["count_total"]
        warning = page_result["warning"]
        incomplete = page_result["incomplete"]
        daraz_code = page_result["daraz_code"]

        created_vals = [str(o.get("created_at") or "") for o in raw_orders]
        updated_vals = [str(o.get("updated_at") or "") for o in raw_orders]
        min_c, max_c = _min_max(created_vals)
        min_u, max_u = _min_max(updated_vals)

        # Reconcile print events + optional header upsert (no item hydration).
        rc0 = time.perf_counter()
        meta_rows: list[tuple[str, str | None, dict[str, Any]]] = []
        for order in raw_orders:
            daraz_oid = str(order.get("order_id") or "")
            if not daraz_oid:
                continue
            local_id = None
            if upsert_headers:
                payload = order_payload_from_daraz(workspace_id, store_uuid, order)
                if payload.get("daraz_order_id"):
                    # Force ready_to_ship group from live status filter
                    payload["status_group"] = "ready_to_ship"
                    row = repo.upsert_daraz_order(payload)
                    local_id = str(row["id"])
            meta_rows.append((daraz_oid, local_id, order))

        print_state = _batch_print_state(
            repo,
            workspace_id,
            store_uuid,
            [(d, lid) for d, lid, _ in meta_rows],
        )

        out_orders: list[dict[str, Any]] = []
        for daraz_oid, local_id, order in meta_rows:
            state = print_state.get(daraz_oid) or {
                "print_count": 0,
                "last_printed_at": None,
            }
            print_count = int(state.get("print_count") or 0)
            out_orders.append(
                {
                    "id": local_id,
                    "store_id": store_uuid,
                    "store_slug": slug,
                    "store_display_name": display,
                    "daraz_order_id": daraz_oid,
                    "order_number": str(order.get("order_number") or "") or None,
                    "status_raw": "ready_to_ship",
                    "status_group": "ready_to_ship",
                    "statuses": order.get("statuses"),
                    "price": order.get("price"),
                    "currency": order.get("currency") or "PKR",
                    "items_count": order.get("items_count"),
                    "created_at_daraz": order.get("created_at"),
                    "updated_at_daraz": order.get("updated_at"),
                    "has_print": print_count > 0,
                    "print_count": print_count,
                    "last_printed_at": state.get("last_printed_at"),
                    "fetch_source": "live_rts",
                }
            )
        reconcile_ms = (time.perf_counter() - rc0) * 1000

        elapsed = int((time.perf_counter() - t0) * 1000)
        ok = warning is None and not incomplete
        return {
            **diag_base,
            "ok": ok,
            "incomplete": incomplete or warning is not None,
            "error": warning,
            "window_mode": window_mode,
            "created_after": used_created_after,
            "update_after": used_update_after,
            "offset_final": offset,
            "pages": pages,
            "daraz_code": daraz_code,
            "countTotal": count_total,
            "returned": len(raw_orders),
            "unique": len(seen),
            "deduped": len(seen),
            "min_created_at": min_c,
            "max_created_at": max_c,
            "min_updated_at": min_u,
            "max_updated_at": max_u,
            "elapsed_ms": elapsed,
            "request_timings": request_timings,
            "timings_ms": {
                "token_refresh": round(token_refresh_ms, 1),
                "orders_get": round(orders_get_ms, 1),
                "normalize": round(normalize_ms, 1),
                "print_reconcile": round(reconcile_ms, 1),
                "total": elapsed,
                "request_timings": request_timings,
            },
            "orders": out_orders,
        }
    except DarazApiError as exc:
        return {
            **diag_base,
            "ok": False,
            "incomplete": True,
            "error": f"daraz:{exc.code}:{str(exc)[:160]}",
            "window_mode": window_mode,
            "created_after": used_created_after,
            "update_after": used_update_after,
            "request_timings": request_timings,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "orders": [],
            "returned": 0,
            "unique": 0,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Live RTS failed store=%s", slug)
        return {
            **diag_base,
            "ok": False,
            "incomplete": True,
            "error": f"{type(exc).__name__}:{str(exc)[:160]}",
            "window_mode": window_mode,
            "created_after": used_created_after,
            "update_after": used_update_after,
            "request_timings": request_timings,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "orders": [],
            "returned": 0,
            "unique": 0,
        }


def load_shipping_rts(
    workspace_id: str,
    *,
    store_ids: list[str],
    upsert_headers: bool = True,
) -> dict[str, Any]:
    """Load live RTS for explicitly selected stores (empty ≠ all)."""
    if not store_ids:
        raise ValueError("No stores selected. Pick at least one store.")

    repo = get_repo()
    stores: list[dict[str, Any]] = []
    for sid in store_ids:
        store = repo.get_store(workspace_id, sid) or repo.get_store_by_uuid(
            workspace_id, sid
        )
        if not store:
            raise ValueError(f"Unknown store: {sid}")
        stores.append(store)

    t0 = time.perf_counter()
    results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=min(STORE_CONCURRENCY, len(stores))) as pool:
        futs = {
            pool.submit(
                fetch_store_live_rts,
                workspace_id,
                store,
                upsert_headers=upsert_headers,
            ): store
            for store in stores
        }
        for fut in as_completed(futs):
            results.append(fut.result())

    # Stable order matching request store order
    by_slug = {r.get("store_id"): r for r in results}
    ordered = []
    for store in stores:
        slug = str(store.get("store_id") or "")
        if slug in by_slug:
            ordered.append(by_slug[slug])

    merged: list[dict[str, Any]] = []
    for r in ordered:
        merged.extend(r.get("orders") or [])

    ok_stores = sum(1 for r in ordered if r.get("ok"))
    failed = [r for r in ordered if not r.get("ok")]
    total_elapsed = int((time.perf_counter() - t0) * 1000)

    # Strip heavy raw from store summaries for response
    store_summaries = []
    for r in ordered:
        store_summaries.append(
            {
                "store_uuid": r.get("store_uuid"),
                "store_id": r.get("store_id"),
                "display_name": r.get("display_name"),
                "ok": r.get("ok"),
                "incomplete": r.get("incomplete"),
                "error": r.get("error"),
                "countTotal": r.get("countTotal"),
                "unique": r.get("unique"),
                "returned": r.get("returned"),
                "elapsed_ms": r.get("elapsed_ms"),
                "min_created_at": r.get("min_created_at"),
                "max_created_at": r.get("max_created_at"),
                "created_after": r.get("created_after"),
                "update_after": r.get("update_after"),
                "window_mode": r.get("window_mode"),
                "pages": r.get("pages"),
                "timings_ms": r.get("timings_ms"),
                "request_timings": r.get("request_timings"),
            }
        )

    slowest_store_ms = max((r.get("elapsed_ms") or 0) for r in ordered) if ordered else 0
    serialize_ms = 0.0  # included in wall clock; no separate serialize step

    return {
        "source": "live_daraz_rts",
        "partial": len(failed) > 0,
        "stores_requested": len(stores),
        "stores_ok": ok_stores,
        "stores_failed": len(failed),
        "orders": merged,
        "count": len(merged),
        "unprinted_count": sum(1 for o in merged if not o.get("has_print")),
        "elapsed_ms": total_elapsed,
        "concurrency": STORE_CONCURRENCY,
        "rts_update_after_days": RTS_UPDATE_AFTER_DAYS,
        "rts_created_after_days": RTS_CREATED_AFTER_DAYS,
        "timings_ms": {
            "wall_total": total_elapsed,
            "slowest_store": slowest_store_ms,
            "serialize": serialize_ms,
            # Wall clock is max(store) under concurrency — never sum store durations.
            "stores": [
                {
                    "store_id": r.get("store_id"),
                    **(r.get("timings_ms") or {"total": r.get("elapsed_ms")}),
                }
                for r in ordered
            ],
        },
        "stores": store_summaries,
    }
