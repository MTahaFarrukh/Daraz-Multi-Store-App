"""Read-only GetSellerItemLimit preflight (create stays blocked when limit hit)."""

from __future__ import annotations

from typing import Any, Callable


def interpret_seller_item_limit(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Map GetSellerItemLimit response fields (Lazada Open API names).

    Documented response data keys (third-party Lazada client typings):
    ``onlineItemCount``, ``itemLimit``, optional ``payItemCnt`` / ``payByrCnt``.
    Daraz PK live shape is **unverified** — we only read these known keys.
    """
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "limit_reached": None,
            "reason": "empty_payload",
            "online_item_count": None,
            "item_limit": None,
            "raw_data_keys": [],
        }
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return {
            "ok": False,
            "limit_reached": None,
            "reason": "missing_data",
            "online_item_count": None,
            "item_limit": None,
            "raw_data_keys": [],
        }
    online = data.get("onlineItemCount")
    if online is None:
        online = data.get("online_item_count")
    limit = data.get("itemLimit")
    if limit is None:
        limit = data.get("item_limit")
    try:
        online_i = int(online) if online is not None else None
    except (TypeError, ValueError):
        online_i = None
    try:
        limit_i = int(limit) if limit is not None else None
    except (TypeError, ValueError):
        limit_i = None

    limit_reached = None
    reason = "ok"
    if online_i is not None and limit_i is not None:
        limit_reached = online_i >= limit_i
        if limit_reached:
            reason = "item_limit_reached"
    elif online_i is None or limit_i is None:
        reason = "fields_incomplete_unverified"
        limit_reached = False

    return {
        "ok": True,
        "limit_reached": bool(limit_reached),
        "reason": reason,
        "online_item_count": online_i,
        "item_limit": limit_i,
        "raw_data_keys": sorted(str(k) for k in data.keys()),
    }


def preflight_seller_item_limit(
    get_limit: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Call GetSellerItemLimit once; never mutates listings."""
    try:
        payload = get_limit()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "limit_reached": None,
            "blocks_create": True,
            "reason": f"seller_item_limit_failed:{type(exc).__name__}",
            "online_item_count": None,
            "item_limit": None,
            "error_type": type(exc).__name__,
            "error_code": getattr(exc, "code", None),
        }
    interpreted = interpret_seller_item_limit(payload)
    # Call succeeded: block only when counts prove the seller is at/over limit.
    return {
        **interpreted,
        "blocks_create": bool(interpreted.get("limit_reached")),
        "api": "/product/seller/item/limit",
        "mutating": False,
    }
