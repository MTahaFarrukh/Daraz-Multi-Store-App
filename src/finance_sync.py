"""Finance ledger sync + conservative read (no Gross Sales double-count)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.audit_log import audit_event
from src.daraz_api import DarazApiError
from src.db.repo import get_repo
from src.ops import client_for_store
from src.token_refresh import refresh_store_tokens

DEFAULT_LOOKBACK_DAYS = 30

# Fee-like rows may contribute to Known Fees when fee_amount is present.
_KNOWN_FEE_HINTS = frozenset(
    {
        "commission",
        "fee",
        "payment fee",
        "transaction fee",
        "shipping fee",
        "vat",
        "wht",
        "withholding",
        "service fee",
    }
)

# Transaction types we refuse to fold into sales aggregates.
_EXCLUDED_FROM_SALES = frozenset(
    {
        "refund",
        "reversal",
        "chargeback",
        "adjustment",
        "payout",
        "withdrawal",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _txn_rows_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return []
    for key in ("transactions", "transaction_details", "details", "items", "module"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    if isinstance(payload.get("data"), list):
        return [r for r in payload["data"] if isinstance(r, dict)]
    return []


def _payout_rows_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if isinstance(payload.get("data"), list):
        return [r for r in payload["data"] if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("payouts", "payout_status", "statements", "module", "items"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
        if isinstance(rows, dict):
            return [rows]
    return []


def classify_transaction(raw_or_mapped: dict[str, Any]) -> dict[str, Any]:
    """Conservative classification — unknown types stay out of sales aggregates."""
    tx_type = str(
        raw_or_mapped.get("transaction_type")
        or raw_or_mapped.get("trans_type")
        or ""
    ).strip().lower()
    fee_type = str(
        raw_or_mapped.get("fee_type") or raw_or_mapped.get("fee_name") or ""
    ).strip().lower()
    amount = _parse_money(
        raw_or_mapped.get("amount")
        if "amount" in raw_or_mapped
        else raw_or_mapped.get("paid_amount")
    )
    fee_amount = _parse_money(
        raw_or_mapped.get("fee_amount")
        if "fee_amount" in raw_or_mapped
        else (
            raw_or_mapped.get("VAT_in_amount")
            or raw_or_mapped.get("WHT_amount")
        )
    )

    excluded = any(h in tx_type for h in _EXCLUDED_FROM_SALES)
    fee_hint = any(h in fee_type or h in tx_type for h in _KNOWN_FEE_HINTS)

    include_fee = fee_amount is not None and bool(fee_hint or fee_type)
    # Never treat ledger amount as Gross Sales — orders own that metric.
    return {
        "transaction_type": raw_or_mapped.get("transaction_type")
        or raw_or_mapped.get("trans_type"),
        "fee_type": raw_or_mapped.get("fee_type") or raw_or_mapped.get("fee_name"),
        "amount": amount,
        "fee_amount": fee_amount,
        "include_in_known_fees": bool(include_fee and not excluded),
        "include_in_gross_sales": False,
        "classification": (
            "excluded"
            if excluded
            else ("fee" if include_fee else ("unknown" if amount is not None else "empty"))
        ),
    }


def map_transaction_row(
    *,
    workspace_id: str,
    store_uuid: str,
    store_slug: str | None,
    raw: dict[str, Any],
) -> dict[str, Any]:
    source_id = (
        raw.get("transaction_number")
        or raw.get("transaction_id")
        or raw.get("id")
        or raw.get("reference")
        or f"{raw.get('order_no')}-{raw.get('orderItem_no')}-{raw.get('fee_type')}-{raw.get('transaction_date')}"
    )
    classified = classify_transaction(raw)
    return {
        "workspace_id": workspace_id,
        "store_id": store_uuid,
        "store_slug": store_slug,
        "source_transaction_id": str(source_id),
        "order_no": raw.get("order_no") or raw.get("order_number"),
        "order_item_no": raw.get("orderItem_no") or raw.get("order_item_no"),
        "transaction_type": classified["transaction_type"],
        "fee_type": classified["fee_type"],
        "amount": classified["amount"],
        "fee_amount": classified["fee_amount"],
        "currency": raw.get("currency") or raw.get("currency_code") or "PKR",
        "payout_status": raw.get("paid_status") or raw.get("payout_status"),
        "transaction_at": raw.get("transaction_date")
        or raw.get("transaction_time")
        or raw.get("created_at"),
        "statement": raw.get("statement") or raw.get("statement_number"),
        "synced_at": _now_iso(),
        "classification": classified["classification"],
        "include_in_known_fees": classified["include_in_known_fees"],
    }


def map_payout_row(
    *,
    workspace_id: str,
    store_uuid: str,
    store_slug: str | None,
    raw: dict[str, Any],
) -> dict[str, Any]:
    source_id = (
        raw.get("statement_number")
        or raw.get("payout_id")
        or raw.get("id")
        or raw.get("created_at")
    )
    return {
        "workspace_id": workspace_id,
        "store_id": store_uuid,
        "store_slug": store_slug,
        "source_payout_id": str(source_id),
        "statement_number": raw.get("statement_number"),
        "status": (
            "paid"
            if raw.get("paid") in {True, "true", "1", 1}
            else (raw.get("status") or "unknown")
        ),
        "payout_amount": _parse_money(raw.get("payout")),
        "item_revenue": _parse_money(raw.get("item_revenue")),
        "fees_total": _parse_money(raw.get("fees_total")),
        "currency": raw.get("currency") or "PKR",
        "created_at_source": raw.get("created_at"),
        "updated_at_source": raw.get("updated_at"),
        "synced_at": _now_iso(),
    }


def sync_finance(
    workspace_id: str,
    *,
    store_ids: list[str],
    actor_user_id: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, Any]:
    if not store_ids:
        raise ValueError("No stores selected. Pick at least one store.")
    repo = get_repo()
    audit_event(
        repo,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action="finance.sync.requested",
        entity_type="finance",
        metadata={
            "store_ids": store_ids,
            "lookback_days": lookback_days,
        },
    )

    try:
        refresh_store_tokens(
            store_ids=store_ids,
            within_minutes=30,
            workspace_id=workspace_id,
        )
    except Exception:  # noqa: BLE001
        pass

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, min(int(lookback_days), 90)))
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")
    created_after = start.strftime("%Y-%m-%dT%H:%M:%S+0000")

    store_results: list[dict[str, Any]] = []
    total_txn = 0
    total_payout = 0

    for sid in store_ids:
        store = repo.get_store(workspace_id, sid) or repo.get_store_by_uuid(
            workspace_id, sid
        )
        if not store:
            store_results.append(
                {
                    "store_id": sid,
                    "status": "error",
                    "error_code": "store_not_found",
                    "transactions": 0,
                    "payouts": 0,
                }
            )
            continue
        slug = str(store.get("store_id") or sid)
        try:
            client = client_for_store(store)
            txn_count = 0
            offset = 0
            while offset < 1000:
                payload = client.get_finance_transaction_details(
                    start_time=start_s,
                    end_time=end_s,
                    offset=offset,
                    limit=100,
                )
                rows = _txn_rows_from_payload(payload if isinstance(payload, dict) else {})
                if not rows:
                    break
                for raw in rows:
                    mapped = map_transaction_row(
                        workspace_id=workspace_id,
                        store_uuid=str(store["id"]),
                        store_slug=slug,
                        raw=raw,
                    )
                    # Persist only stable ledger fields (no classification columns required).
                    persist = {
                        k: v
                        for k, v in mapped.items()
                        if k
                        not in {"classification", "include_in_known_fees"}
                    }
                    repo.upsert_finance_transaction(persist)
                    txn_count += 1
                if len(rows) < 100:
                    break
                offset += 100

            payout_payload = client.get_payout_status(created_after=created_after)
            payout_rows = _payout_rows_from_payload(
                payout_payload if isinstance(payout_payload, dict) else {}
            )
            payout_count = 0
            for raw in payout_rows:
                mapped = map_payout_row(
                    workspace_id=workspace_id,
                    store_uuid=str(store["id"]),
                    store_slug=slug,
                    raw=raw,
                )
                repo.upsert_finance_payout(mapped)
                payout_count += 1

            total_txn += txn_count
            total_payout += payout_count
            store_results.append(
                {
                    "store_id": slug,
                    "status": "ok",
                    "transactions": txn_count,
                    "payouts": payout_count,
                }
            )
        except DarazApiError as exc:
            store_results.append(
                {
                    "store_id": slug,
                    "status": "error",
                    "error_code": str(exc.code or "daraz_error"),
                    "transactions": 0,
                    "payouts": 0,
                }
            )
        except Exception as exc:  # noqa: BLE001
            store_results.append(
                {
                    "store_id": slug,
                    "status": "error",
                    "error_code": type(exc).__name__,
                    "transactions": 0,
                    "payouts": 0,
                }
            )

    failed = [r for r in store_results if r.get("status") != "ok"]
    status = (
        "failed"
        if len(failed) == len(store_results)
        else ("partial" if failed else "completed")
    )
    audit_event(
        repo,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action=(
            "finance.sync.failed"
            if status == "failed"
            else "finance.sync.completed"
        ),
        entity_type="finance",
        metadata={
            "status": status,
            "store_ids": store_ids,
            "transactions": total_txn,
            "payouts": total_payout,
            "failed_stores": [f.get("store_id") for f in failed],
            "date_from": start_s,
            "date_to": end_s,
        },
    )
    return {
        "status": status,
        "transactions_synced": total_txn,
        "payouts_synced": total_payout,
        "store_results": store_results,
        "date_from": start_s,
        "date_to": end_s,
        "synced_at": _now_iso(),
        "partial": status == "partial",
    }


def _orders_gross_sales(workspace_id: str, *, store_id: str | None = None) -> float | None:
    """Canonical Gross Sales = sum of local order.price (performance/order model)."""
    repo = get_repo()
    stores = {str(s["id"]): s for s in repo.list_stores(workspace_id)}
    slug_map = {str(s.get("store_id") or "").lower(): str(s["id"]) for s in stores.values()}
    store_uuid = None
    if store_id:
        store_uuid = store_id if store_id in stores else slug_map.get(store_id.lower())
    result = repo.list_orders(
        workspace_id,
        {
            "store_uuids": [store_uuid] if store_uuid else None,
            "page": 1,
            "page_size": 200,
        },
    )
    items = list(result.get("items") or [])
    total = int(result.get("total") or len(items))
    page = 2
    while len(items) < total and page <= 50:
        more = repo.list_orders(
            workspace_id,
            {
                "store_uuids": [store_uuid] if store_uuid else None,
                "page": page,
                "page_size": 200,
            },
        )
        batch = more.get("items") or []
        if not batch:
            break
        items.extend(batch)
        page += 1
    if not items:
        return None
    gross = 0.0
    for o in items:
        try:
            gross += float(o.get("price") or 0)
        except (TypeError, ValueError):
            continue
    return round(gross, 2)


def finance_summary(workspace_id: str, *, store_id: str | None = None) -> dict[str, Any]:
    repo = get_repo()
    txns = repo.list_finance_transactions(
        workspace_id, store_id=store_id, page=1, page_size=5000
    )
    payouts = repo.list_finance_payouts(
        workspace_id, store_id=store_id, page=1, page_size=5000
    )
    items = txns.get("items") or []
    pitems = payouts.get("items") or []

    # Canonical Gross Sales from orders — never sum finance ledger amounts.
    gross_sales = _orders_gross_sales(workspace_id, store_id=store_id)

    known_fees_parts: list[float] = []
    unknown_txn = 0
    for t in items:
        classified = classify_transaction(t)
        if classified["include_in_known_fees"] and classified["fee_amount"] is not None:
            known_fees_parts.append(float(classified["fee_amount"]))
        elif classified["classification"] == "unknown":
            unknown_txn += 1

    fee_totals = [p.get("fees_total") for p in pitems if p.get("fees_total") is not None]
    known_fees_parts.extend(float(x) for x in fee_totals)

    payout_amts = [
        p.get("payout_amount") for p in pitems if p.get("payout_amount") is not None
    ]

    last_synced = None
    for row in items + pitems:
        ts = row.get("synced_at")
        if ts and (last_synced is None or str(ts) > str(last_synced)):
            last_synced = ts

    known_fees = round(sum(known_fees_parts), 2) if known_fees_parts else None
    known_payouts = (
        round(sum(float(x) for x in payout_amts), 2) if payout_amts else None
    )

    if not items and not pitems:
        completeness_level = "unknown"
    elif unknown_txn or (items and known_fees is None and known_payouts is None):
        completeness_level = "partial"
    else:
        completeness_level = "partial" if (items or pitems) else "unknown"
    if items and pitems and known_fees is not None and known_payouts is not None:
        completeness_level = "complete"

    return {
        "gross_sales": gross_sales,
        "known_fees": known_fees,
        "known_payouts": known_payouts,
        "transaction_count": len(items),
        "payout_count": len(pitems),
        "last_synced": last_synced,
        "completeness": {
            "level": completeness_level,
            "has_transactions": bool(items),
            "has_payouts": bool(pitems),
            "fees_known": known_fees is not None,
            "payouts_known": bool(payout_amts),
            "unknown_transactions": unknown_txn,
            # Explicit: ledger amounts are NOT folded into Gross Sales.
            "gross_sales_source": "orders",
            "finance_ledger_excluded_from_gross_sales": True,
        },
        "metric_notes": {
            "gross_sales": (
                "Canonical Gross Sales from local order prices "
                "(same model as Analytics/Performance). "
                "Finance ledger amounts are never added."
            ),
            "known_fees": (
                "Only explicitly mapped fee amounts from synced finance rows — "
                "not a complete P&L. Unknown transaction types are excluded."
            ),
            "known_payouts": "Sum of known payout amounts — may be incomplete.",
        },
    }
