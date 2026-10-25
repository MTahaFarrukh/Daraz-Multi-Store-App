"""Read-only Daraz connectivity probe — never mutates listings/stock/prices/orders."""

from __future__ import annotations

import time
from typing import Any

from src.daraz_api import DarazApiError
from src.db.repo import get_repo
from src.token_store import connection_health, sanitize_store_view


def probe_store_api_health(workspace_id: str, store_ref: str) -> dict[str, Any]:
    """Call GET /seller/get for one store (read-only).

    Distinguishes token-expiry heuristics from verified API connectivity.
    Never returns tokens or secrets.
    """
    repo = get_repo()
    store = repo.get_store(workspace_id, store_ref) or repo.get_store_by_uuid(
        workspace_id, store_ref
    )
    if not store:
        return {
            "ok": False,
            "store": None,
            "token_status": "unknown",
            "api_health": "error",
            "reason": "store_not_found",
        }

    token = connection_health(store)
    view = sanitize_store_view(store)
    # Override list-default unverified with probe result below.
    view["token_status"] = token.get("token_status") or "unknown"

    if token.get("connection_status") == "needs_reconnection":
        view["api_health"] = "skipped"
        return {
            "ok": False,
            "store": view,
            "token_status": view["token_status"],
            "api_health": "skipped",
            "reason": "refresh_token_expired_reconnect_required",
            "checked_at": _iso_now(),
            "probe": {"api": "/seller/get", "status": "skipped"},
        }

    t0 = time.perf_counter()
    try:
        from src.ops import client_for_store
        from src.token_refresh import refresh_store_tokens

        try:
            refresh_store_tokens(
                store_ids=[str(store.get("store_id") or store.get("id"))],
                within_minutes=30,
                workspace_id=workspace_id,
            )
            store = (
                repo.get_store(workspace_id, store_ref)
                or repo.get_store_by_uuid(workspace_id, store_ref)
                or store
            )
            view = sanitize_store_view(store)
            view["token_status"] = connection_health(store).get("token_status") or "unknown"
        except Exception:  # noqa: BLE001 — probe still attempts seller/get
            pass

        client = client_for_store(store)
        payload = client.get_seller()
    except DarazApiError as exc:
        view["api_health"] = "error"
        return {
            "ok": False,
            "store": view,
            "token_status": view.get("token_status") or "unknown",
            "api_health": "error",
            "reason": f"daraz_api:{exc.code or type(exc).__name__}",
            "checked_at": _iso_now(),
            "probe": {
                "api": "/seller/get",
                "status": "error",
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            },
        }
    except Exception as exc:  # noqa: BLE001
        view["api_health"] = "error"
        return {
            "ok": False,
            "store": view,
            "token_status": view.get("token_status") or "unknown",
            "api_health": "error",
            "reason": f"probe_error:{type(exc).__name__}",
            "checked_at": _iso_now(),
            "probe": {
                "api": "/seller/get",
                "status": "error",
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    code = str((payload or {}).get("code") or "")
    latency = round((time.perf_counter() - t0) * 1000, 1)
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    seller_name = None
    seller_id = None
    if isinstance(data, dict):
        seller_name = data.get("name") or data.get("short_code")
        seller_id = data.get("seller_id") or data.get("user_id")

    if code not in {"0", "Success", "success", ""}:
        view["api_health"] = "error"
        return {
            "ok": False,
            "store": view,
            "token_status": view.get("token_status") or "unknown",
            "api_health": "error",
            "reason": f"unexpected_code:{code or 'missing'}",
            "checked_at": _iso_now(),
            "probe": {"api": "/seller/get", "status": "error", "latency_ms": latency},
            "seller": _public_seller(seller_name, seller_id),
        }

    view["api_health"] = "ok"
    return {
        "ok": True,
        "store": view,
        "token_status": view.get("token_status") or "unknown",
        "api_health": "ok",
        "reason": "seller_get_ok",
        "checked_at": _iso_now(),
        "probe": {"api": "/seller/get", "status": "ok", "latency_ms": latency},
        "seller": _public_seller(seller_name, seller_id),
    }


def _public_seller(name: Any, seller_id: Any) -> dict[str, Any]:
    return {
        "name": str(name).strip() if name is not None and str(name).strip() else None,
        "seller_id": str(seller_id).strip()
        if seller_id is not None and str(seller_id).strip()
        else None,
    }


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat()
