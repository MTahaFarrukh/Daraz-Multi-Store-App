"""Conservative Daraz finance sync + read (transactions / payouts)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.audit_log import audit_event
from src.daraz_api import DarazApiError
from src.db.repo import get_repo
from src.ops import client_for_store
from src.token_refresh import refresh_store_tokens

DEFAULT_LOOKBACK_DAYS = 30


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
    # Sometimes data itself is a list
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
    return {
        "workspace_id": workspace_id,
        "store_id": store_uuid,
        "store_slug": store_slug,
        "source_transaction_id": str(source_id),
        "order_no": raw.get("order_no") or raw.get("order_number"),
        "order_item_no": raw.get("orderItem_no") or raw.get("order_item_no"),
        "transaction_type": raw.get("transaction_type") or raw.get("trans_type"),
        "fee_type": raw.get("fee_type") or raw.get("fee_name"),
        "amount": _parse_money(raw.get("amount") or raw.get("paid_amount")),
        "fee_amount": _parse_money(
            raw.get("fee_amount") or raw.get("VAT_in_amount") or raw.get("WHT_amount")
        ),
        "currency": raw.get("currency") or raw.get("currency_code") or "PKR",
        "payout_status": raw.get("paid_status") or raw.get("payout_status"),
        "transaction_at": raw.get("transaction_date")
        or raw.get("transaction_time")
        or raw.get("created_at"),
        "statement": raw.get("statement") or raw.get("statement_number"),
        "synced_at": _now_iso(),
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
                    repo.upsert_finance_transaction(mapped)
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

    amounts = [t.get("amount") for t in items if t.get("amount") is not None]
    fees = [t.get("fee_amount") for t in items if t.get("fee_amount") is not None]
    payout_amts = [
        p.get("payout_amount") for p in pitems if p.get("payout_amount") is not None
    ]
    fee_totals = [p.get("fees_total") for p in pitems if p.get("fees_total") is not None]

    last_synced = None
    for row in list(items)[:1] + list(pitems)[:1]:
        ts = row.get("synced_at")
        if ts and (last_synced is None or str(ts) > str(last_synced)):
            last_synced = ts
    for row in items + pitems:
        ts = row.get("synced_at")
        if ts and (last_synced is None or str(ts) > str(last_synced)):
            last_synced = ts

    known_fees = None
    if fees or fee_totals:
        known_fees = round(sum(float(x) for x in fees) + sum(float(x) for x in fee_totals), 2)

    return {
        "gross_sales": round(sum(float(x) for x in amounts), 2) if amounts else None,
        "known_fees": known_fees,
        "known_payouts": round(sum(float(x) for x in payout_amts), 2) if payout_amts else None,
        "transaction_count": len(items),
        "payout_count": len(pitems),
        "last_synced": last_synced,
        "completeness": {
            "has_transactions": bool(items),
            "has_payouts": bool(pitems),
            "amounts_known": bool(amounts),
            "fees_known": known_fees is not None,
            "payouts_known": bool(payout_amts),
        },
        "metric_notes": {
            "gross_sales": "Sum of known transaction amounts from synced finance data.",
            "known_fees": "Only fees present in synced rows — not a complete P&L.",
            "known_payouts": "Sum of known payout amounts — may be incomplete.",
        },
    }
