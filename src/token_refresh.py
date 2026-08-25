"""Token refresh helpers for multi-store token storage."""

from __future__ import annotations

from typing import Any

from src.config import DEFAULT_API_BASE, get_env, require_env
from src.daraz_api import DarazApiError, DarazClient
from src.token_store import (
    access_token_expires_soon,
    build_token_record,
    get_store,
    list_stores,
    upsert_store,
)


def _client_without_token() -> DarazClient:
    return DarazClient(
        app_key=require_env("DARAZ_APP_KEY"),
        app_secret=require_env("DARAZ_APP_SECRET"),
        api_base=get_env("DARAZ_API_BASE", DEFAULT_API_BASE),
    )


def refresh_one_store(store: dict[str, Any]) -> dict[str, Any]:
    """Refresh tokens for one store record and persist the result."""
    refresh = store.get("refresh_token")
    if not refresh:
        raise ValueError(
            f"Store {store.get('store_id')} has no refresh_token - re-run OAuth"
        )

    client = _client_without_token()
    response = client.refresh_token(str(refresh))
    # Preserve identity fields Daraz may omit on refresh.
    merged = {
        **response,
        "account": response.get("account") or store.get("account", ""),
        "country_user_info": response.get("country_user_info")
        or store.get("country_user_info", []),
        "country": response.get("country") or store.get("country", ""),
    }
    record = build_token_record(merged)
    record["store_id"] = store.get("store_id") or record["store_id"]
    record["store_name"] = store.get("store_name") or record["store_name"]
    record["seller_id"] = record.get("seller_id") or store.get("seller_id", "")
    return upsert_store(record)


def refresh_store_tokens(
    store_id: str | None = None,
    *,
    force: bool = False,
    within_minutes: int = 60,
) -> list[dict[str, Any]]:
    """
    Refresh one store or all stores whose access token expires soon.

    Returns list of result dicts: {store_id, status, error?}.
    """
    if store_id:
        store = get_store(store_id)
        if not store:
            raise ValueError(f"Unknown store_id: {store_id}")
        targets = [store]
    else:
        targets = list_stores()

    results: list[dict[str, Any]] = []
    for store in targets:
        sid = str(store.get("store_id", ""))
        if not force and not access_token_expires_soon(store, within_minutes=within_minutes):
            results.append({"store_id": sid, "status": "skipped", "reason": "not_expiring_soon"})
            continue
        try:
            updated = refresh_one_store(store)
            results.append(
                {
                    "store_id": updated.get("store_id", sid),
                    "status": "refreshed",
                    "access_token_expires_at": updated.get("access_token_expires_at"),
                }
            )
        except (DarazApiError, ValueError) as exc:
            results.append(
                {
                    "store_id": sid,
                    "status": "error",
                    "error": str(exc),
                    "daraz_code": getattr(exc, "code", None),
                }
            )
    return results
