"""Phase 3 hotfix diagnostics: live RTS per store (no tokens/PII logged)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.daraz_api import DarazApiError
from src.ops import client_for_store
from src.orders import extract_orders
from src.token_store import list_stores, sanitize_store_view

OUT = Path("data/phase3_hotfix_rts_diag.json")
PAGE = 50


def _iso_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).astimezone().replace(
        microsecond=0
    ).isoformat()


def _safe_stats(orders: list[dict[str, Any]]) -> dict[str, Any]:
    created = [str(o.get("created_at") or "") for o in orders if o.get("created_at")]
    updated = [str(o.get("updated_at") or "") for o in orders if o.get("updated_at")]
    statuses: list[str] = []
    for o in orders:
        st = o.get("statuses")
        if isinstance(st, list):
            statuses.extend(str(x) for x in st)
        elif st:
            statuses.append(str(st))
    return {
        "returned": len(orders),
        "min_created_at": min(created) if created else None,
        "max_created_at": max(created) if created else None,
        "min_updated_at": min(updated) if updated else None,
        "max_updated_at": max(updated) if updated else None,
        "status_samples": sorted(set(statuses))[:20],
        "order_ids_sample": [str(o.get("order_id")) for o in orders[:8]],
    }


def probe(client, label: str, **kwargs) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "label": label,
        "request": {k: v for k, v in kwargs.items() if v is not None},
        "ok": False,
    }
    t0 = time.perf_counter()
    try:
        resp = client.get_orders(**kwargs)
        elapsed = int((time.perf_counter() - t0) * 1000)
        data = resp.get("data") or {}
        orders = extract_orders(resp)
        entry.update(
            {
                "ok": True,
                "elapsed_ms": elapsed,
                "code": resp.get("code"),
                "countTotal": data.get("countTotal")
                or data.get("count_total")
                or data.get("total_count"),
                **_safe_stats(orders),
            }
        )
    except DarazApiError as exc:
        entry.update(
            {
                "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                "error": str(exc),
                "error_code": exc.code,
            }
        )
    except Exception as exc:  # noqa: BLE001
        entry.update(
            {
                "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                "error": f"{type(exc).__name__}:{exc}",
            }
        )
    return entry


def fetch_all_rts(client, *, created_after: str | None, update_after: str | None) -> dict[str, Any]:
    seen: set[str] = set()
    all_orders: list[dict[str, Any]] = []
    offset = 0
    pages = []
    warning = None
    t0 = time.perf_counter()
    count_total = None
    while offset <= 5000:
        kwargs: dict[str, Any] = {
            "status": "ready_to_ship",
            "limit": PAGE,
            "offset": offset,
            "sort_by": "updated_at",
            "sort_direction": "DESC",
        }
        if created_after:
            kwargs["created_after"] = created_after
        if update_after:
            kwargs["update_after"] = update_after
        page = probe(client, f"page offset={offset}", **kwargs)
        pages.append(page)
        if not page.get("ok"):
            warning = page.get("error")
            break
        count_total = page.get("countTotal") if page.get("countTotal") is not None else count_total
        # re-fetch raw for accumulation
        resp = client.get_orders(**kwargs)
        orders = extract_orders(resp)
        if not orders:
            break
        overlap = 0
        for o in orders:
            oid = str(o.get("order_id") or "")
            if not oid:
                continue
            if oid in seen:
                overlap += 1
                continue
            seen.add(oid)
            all_orders.append(o)
        if overlap:
            warning = "pagination_overlap"
            break
        if len(orders) < PAGE:
            break
        offset += PAGE
    incomplete = False
    if count_total is not None:
        try:
            if int(count_total) > len(seen):
                incomplete = True
                warning = warning or "countTotal_mismatch"
        except (TypeError, ValueError):
            pass
    return {
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "unique": len(seen),
        "countTotal": count_total,
        "incomplete": incomplete,
        "warning": warning,
        "pages": pages,
        **_safe_stats(all_orders),
    }


def main() -> int:
    stores = list_stores()
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "stores": [],
        "notes": [
            "Shipping currently loads local daraz_orders status_group=ready_to_ship",
            "This script queries Daraz live only",
        ],
    }
    for store in stores:
        view = sanitize_store_view(store)
        client = client_for_store(store)
        client.timeout = 60.0
        block: dict[str, Any] = {
            "store_id": view.get("store_id"),
            "display_name": view.get("display_name"),
            "account": view.get("account"),
            "seller_id": view.get("seller_id"),
            "probes": {},
        }
        # A: no date window
        block["probes"]["rts_no_date"] = probe(
            client,
            "RTS no date",
            status="ready_to_ship",
            limit=PAGE,
            offset=0,
        )
        # B: created_after 30d (current Shipping live default pattern)
        block["probes"]["rts_created_30d"] = fetch_all_rts(
            client, created_after=_iso_ago(30), update_after=None
        )
        # C: created_after 90d
        block["probes"]["rts_created_90d"] = fetch_all_rts(
            client, created_after=_iso_ago(90), update_after=None
        )
        # D: update_after 30d
        block["probes"]["rts_update_30d"] = fetch_all_rts(
            client, created_after=None, update_after=_iso_ago(30)
        )
        # E: update_after 7d (old sync default)
        block["probes"]["rts_update_7d"] = fetch_all_rts(
            client, created_after=None, update_after=_iso_ago(7)
        )
        report["stores"].append(block)
        print(
            view.get("store_id"),
            "no_date",
            block["probes"]["rts_no_date"].get("ok"),
            block["probes"]["rts_no_date"].get("error_code")
            or block["probes"]["rts_no_date"].get("returned"),
            "c30",
            block["probes"]["rts_created_30d"].get("unique"),
            "c90",
            block["probes"]["rts_created_90d"].get("unique"),
            "u30",
            block["probes"]["rts_update_30d"].get("unique"),
            "u7",
            block["probes"]["rts_update_7d"].get("unique"),
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
