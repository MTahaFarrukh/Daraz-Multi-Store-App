"""
Phase 2.5A — READ-ONLY Daraz store performance capability spike.

Usage:
  python scripts/spike_store_performance.py

Never prints access/refresh tokens or app secrets.
Does not mutate seller data (no pack/RTS/cancel/product updates).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.daraz_api import DarazApiError
from src.ops import client_for_store, resolve_stores
from src.orders import extract_order_items, extract_orders

PK = timezone(timedelta(hours=5))
SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "app_secret",
    "token",
    "authorization",
    "password",
}


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=PK)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=PK)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=PK)
    return start.isoformat(), end.isoformat()


def _sanitize(obj: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "…"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(s in lk for s in SENSITIVE_KEYS):
                out[k] = "***"
            elif lk in {"phone", "phone2", "address1", "address2", "address3", "customer_first_name", "customer_last_name"}:
                out[k] = "***"
            else:
                out[k] = _sanitize(v, depth + 1)
        return out
    if isinstance(obj, list):
        if len(obj) > 3:
            return [_sanitize(x, depth + 1) for x in obj[:2]] + [f"…(+{len(obj) - 2} more)"]
        return [_sanitize(x, depth + 1) for x in obj]
    if isinstance(obj, str) and len(obj) > 200:
        return obj[:80] + "…"
    return obj


def _field_inventory(rows: list[dict[str, Any]]) -> list[str]:
    keys: set[str] = set()
    for row in rows:
        keys.update(str(k) for k in row.keys())
    return sorted(keys)


def _probe(name: str, fn) -> dict[str, Any]:
    try:
        payload = fn()
        code = str(payload.get("code", ""))
        data = payload.get("data")
        result: dict[str, Any] = {
            "probe": name,
            "ok": code == "0",
            "api_code": code,
            "request_id": payload.get("request_id"),
            "top_keys": sorted(payload.keys()),
        }
        if isinstance(data, dict):
            result["data_keys"] = sorted(data.keys())
            orders = data.get("orders")
            if isinstance(orders, list):
                result["count"] = data.get("count")
                result["countTotal"] = data.get("countTotal")
                result["page_len"] = len(orders)
                result["order_fields"] = _field_inventory([o for o in orders if isinstance(o, dict)])
                if orders and isinstance(orders[0], dict):
                    result["sample_order"] = _sanitize(orders[0])
            else:
                # order/get returns order under data; items may be list
                if "order_id" in data or "order_number" in data:
                    result["order_fields"] = _field_inventory([data])
                    result["sample_order"] = _sanitize(data)
                result["data_sample"] = _sanitize(data)
        elif isinstance(data, list):
            result["page_len"] = len(data)
            dicts = [x for x in data if isinstance(x, dict)]
            result["item_fields"] = _field_inventory(dicts)
            if dicts:
                result["sample_item"] = _sanitize(dicts[0])
                money_keys = [
                    k
                    for k in result["item_fields"]
                    if any(
                        x in k.lower()
                        for x in ("price", "voucher", "fee", "tax", "paid", "amount", "shipping")
                    )
                ]
                result["money_related_fields"] = money_keys
                result["sample_item_money"] = _sanitize({k: dicts[0].get(k) for k in money_keys})
                result["item_status_counts"] = dict(
                    Counter(str(i.get("status") or "") for i in dicts)
                )
                result["item_count"] = len(dicts)
        else:
            result["data_type"] = type(data).__name__
            result["data_sample"] = _sanitize(data)
        return result
    except DarazApiError as exc:
        return {
            "probe": name,
            "ok": False,
            "http_status": exc.http_status,
            "api_code": exc.code,
            "message": str(exc)[:240],
            "request_id": exc.request_id,
            "payload_keys": sorted((exc.payload or {}).keys()) if isinstance(exc.payload, dict) else [],
        }
    except Exception as exc:  # noqa: BLE001 — network timeouts etc.
        return {"probe": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:240]}


def main() -> int:
    stores = resolve_stores()
    if not stores:
        print(json.dumps({"error": "No connected stores in local vault"}, indent=2))
        return 1

    store = stores[0]
    store_label = {
        "store_id": store.get("store_id"),
        "display_name": store.get("display_name") or store.get("store_name"),
        "seller_id": store.get("seller_id"),
        "country": store.get("country"),
        "account": "***" if store.get("account") else None,
    }
    client = client_for_store(store)
    client.timeout = 60.0

    now = datetime.now(PK)
    month_start, month_end = _month_bounds(now.year, now.month)
    # Small recent window first
    recent_after = (now - timedelta(days=7)).replace(microsecond=0).isoformat()
    # Previous calendar month
    prev_y, prev_m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    prev_start, prev_end = _month_bounds(prev_y, prev_m)
    # ~90 days back
    hist_after = (now - timedelta(days=90)).replace(microsecond=0).isoformat()

    report: dict[str, Any] = {
        "phase": "2.5A",
        "store": store_label,
        "timezone_assumption": "Asia/Karachi (+05:00)",
        "windows": {
            "recent_7d_after": recent_after,
            "current_month": {"start": month_start, "end": month_end},
            "previous_month": {"start": prev_start, "end": prev_end},
            "hist_90d_after": hist_after,
        },
        "probes": [],
    }

    # --- Orders probes ---
    report["probes"].append(
        _probe(
            "orders_get status=all recent_7d limit=5",
            lambda: client.get_orders(
                created_after=recent_after,
                status="all",
                limit=5,
                offset=0,
                sort_by="created_at",
                sort_direction="DESC",
            ),
        )
    )
    report["probes"].append(
        _probe(
            "orders_get status=all current_month created_before",
            lambda: client.get_orders(
                created_after=month_start,
                created_before=month_end,
                status="all",
                limit=20,
                offset=0,
                sort_by="created_at",
                sort_direction="ASC",
            ),
        )
    )
    report["probes"].append(
        _probe(
            "orders_get status=canceled current_month",
            lambda: client.get_orders(
                created_after=month_start,
                created_before=month_end,
                status="canceled",
                limit=20,
                offset=0,
            ),
        )
    )
    report["probes"].append(
        _probe(
            "orders_get status=returned current_month",
            lambda: client.get_orders(
                created_after=month_start,
                created_before=month_end,
                status="returned",
                limit=20,
                offset=0,
            ),
        )
    )
    report["probes"].append(
        _probe(
            "orders_get status=all previous_month",
            lambda: client.get_orders(
                created_after=prev_start,
                created_before=prev_end,
                status="all",
                limit=5,
                offset=0,
            ),
        )
    )
    report["probes"].append(
        _probe(
            "orders_get status=all hist_90d countTotal check",
            lambda: client.get_orders(
                created_after=hist_after,
                status="all",
                limit=1,
                offset=0,
            ),
        )
    )
    report["probes"].append(
        _probe(
            "orders_get update_after only (no created_after)",
            lambda: client.get_orders(
                update_after=recent_after,
                status="all",
                limit=3,
                offset=0,
            ),
        )
    )

    # Pagination sample: page 0 then page 1 if countTotal suggests more
    page0 = None
    try:
        page0 = client.get_orders(
            created_after=hist_after,
            status="all",
            limit=10,
            offset=0,
            sort_direction="DESC",
        )
        report["probes"].append(
            {
                "probe": "pagination_page0",
                "ok": str(page0.get("code")) == "0",
                "count": (page0.get("data") or {}).get("count"),
                "countTotal": (page0.get("data") or {}).get("countTotal"),
                "page_len": len((page0.get("data") or {}).get("orders") or []),
            }
        )
        page1 = client.get_orders(
            created_after=hist_after,
            status="all",
            limit=10,
            offset=10,
            sort_direction="DESC",
        )
        ids0 = {str(o.get("order_id")) for o in ((page0.get("data") or {}).get("orders") or [])}
        ids1 = {str(o.get("order_id")) for o in ((page1.get("data") or {}).get("orders") or [])}
        report["probes"].append(
            {
                "probe": "pagination_page1_offset_10",
                "ok": str(page1.get("code")) == "0",
                "page_len": len((page1.get("data") or {}).get("orders") or []),
                "overlap_with_page0": len(ids0 & ids1),
                "countTotal": (page1.get("data") or {}).get("countTotal"),
            }
        )
    except DarazApiError as exc:
        report["probes"].append(
            {
                "probe": "pagination",
                "ok": False,
                "api_code": exc.code,
                "message": str(exc)[:240],
            }
        )

    # Order detail + items monetary fields
    sample_order_id = None
    for p in report["probes"]:
        sample = p.get("sample_order")
        if isinstance(sample, dict) and sample.get("order_id"):
            sample_order_id = sample["order_id"]
            break
    if sample_order_id is None and page0:
        orders = extract_orders(page0)
        if orders:
            sample_order_id = orders[0].get("order_id")

    if sample_order_id is not None:
        report["probes"].append(
            _probe(
                f"order_get id={sample_order_id}",
                lambda: client.get_order(sample_order_id),
            )
        )
        report["probes"].append(
            _probe(
                f"order_items_get id={sample_order_id}",
                lambda: client.get_order_items(sample_order_id),
            )
        )

    # Speculative analytics / seller endpoints (expect failures)
    for path, params in [
        ("/seller/get", {}),
        ("/data/order/statistics/get", {}),
        ("/business/advisor/get", {}),
    ]:
        report["probes"].append(
            _probe(
                f"speculative {path}",
                lambda p=path, bp=params: client._request(p, business_params=bp),
            )
        )

    # Finance
    fin_start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    fin_end = now.strftime("%Y-%m-%d")
    report["probes"].append(
        _probe(
            "finance_transaction_details last_30d",
            lambda: client.get_finance_transaction_details(
                start_time=fin_start,
                end_time=fin_end,
                offset=0,
                limit=20,
            ),
        )
    )
    report["probes"].append(
        _probe(
            "finance_transaction_details ISO datetime",
            lambda: client.get_finance_transaction_details(
                start_time=month_start,
                end_time=month_end,
                offset=0,
                limit=10,
            ),
        )
    )
    report["probes"].append(
        _probe(
            "finance_payout_status created_after month_start",
            lambda: client.get_payout_status(created_after=month_start),
        )
    )
    report["probes"].append(
        _probe(
            "finance_payout_status created_after YYYY-MM-DD",
            lambda: client.get_payout_status(created_after=fin_start),
        )
    )

    report["probes"].append(
        _probe("shipment_providers_get", lambda: client.get_shipment_providers())
    )

    out_path = Path(__file__).resolve().parents[1] / "data" / "spike_2_5a_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"wrote": str(out_path), "probes": len(report["probes"])}, indent=2))
    for p in report["probes"]:
        print(
            f"- {p.get('probe')}: ok={p.get('ok')} code={p.get('api_code')} "
            f"countTotal={p.get('countTotal')} msg={(p.get('message') or p.get('error') or '')[:100]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
