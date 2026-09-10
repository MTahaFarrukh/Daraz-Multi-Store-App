"""Token refresh helpers for workspace-scoped store storage."""

from __future__ import annotations

from typing import Any, Callable

from src.config import DEFAULT_API_BASE, get_env, require_env
from src.daraz_api import DarazApiError, DarazClient
from src.db import get_repo
from src.token_store import (
    access_token_expires_soon,
    build_token_record,
    get_store as legacy_get_store,
    list_stores as legacy_list_stores,
    upsert_store as legacy_upsert_store,
)


def _client_without_token() -> DarazClient:
    return DarazClient(
        app_key=require_env("DARAZ_APP_KEY"),
        app_secret=require_env("DARAZ_APP_SECRET"),
        api_base=get_env("DARAZ_API_BASE", DEFAULT_API_BASE),
    )


def refresh_one_store(
    store: dict[str, Any],
    *,
    upsert_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
    record["display_name"] = store.get("display_name") or record.get("display_name")
    record["seller_id"] = record.get("seller_id") or store.get("seller_id", "")
    persist = upsert_fn or legacy_upsert_store
    return persist(record)


def refresh_store_tokens(
    store_id: str | None = None,
    *,
    store_ids: list[str] | None = None,
    force: bool = False,
    within_minutes: int = 60,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Refresh one store, selected stores, or all stores whose access token expires soon.

    When workspace_id is set, uses workspace-scoped storage.
    When omitted, uses legacy global vault (CLI / existing unit tests).
    """
    if workspace_id:
        repo = get_repo()

        def get_one(sid: str) -> dict[str, Any] | None:
            return repo.get_store(workspace_id, sid)

        def list_all() -> list[dict[str, Any]]:
            return repo.list_stores(workspace_id)

        def upsert(record: dict[str, Any]) -> dict[str, Any]:
            return repo.upsert_store(workspace_id, record)
    else:
        get_one = legacy_get_store
        list_all = legacy_list_stores
        upsert = legacy_upsert_store

    if store_ids is not None:
        if not store_ids:
            raise ValueError("No stores selected. Pick at least one store.")
        targets = []
        for sid in store_ids:
            store = get_one(sid)
            if not store:
                raise ValueError(f"Unknown store_id: {sid}")
            targets.append(store)
    elif store_id:
        store = get_one(store_id)
        if not store:
            raise ValueError(f"Unknown store_id: {store_id}")
        targets = [store]
    else:
        targets = list_all()

    results: list[dict[str, Any]] = []
    for store in targets:
        sid = str(store.get("store_id", ""))
        if not force and not access_token_expires_soon(store, within_minutes=within_minutes):
            results.append({"store_id": sid, "status": "skipped", "reason": "not_expiring_soon"})
            continue
        try:
            updated = refresh_one_store(store, upsert_fn=upsert)
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
