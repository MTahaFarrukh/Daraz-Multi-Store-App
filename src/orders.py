"""Shared order/item helpers for smoke test and Phase 3 CLI (read-only)."""

from __future__ import annotations

from typing import Any

LABEL_ELIGIBLE_STATUSES = frozenset(
    {"packed", "ready_to_ship", "ready to ship", "rts", "shipped"}
)


def is_label_eligible(item: dict[str, Any]) -> bool:
    status = str(item.get("status", "")).strip().lower()
    return status in LABEL_ELIGIBLE_STATUSES or "ready" in status or status == "packed"


def order_preview(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": order.get("order_id"),
        "order_number": order.get("order_number"),
        "items_count": order.get("items_count"),
        "statuses": order.get("statuses"),
        "created_at": order.get("created_at"),
    }


def extract_orders(orders_resp: dict[str, Any]) -> list[dict[str, Any]]:
    return list((orders_resp.get("data") or {}).get("orders") or [])


def extract_order_items(items_resp: dict[str, Any]) -> list[dict[str, Any]]:
    data = items_resp.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.get("order_items") or data.get("items") or [])
    return []


def eligible_item_ids(items: list[dict[str, Any]]) -> list[str]:
    return [
        str(i["order_item_id"])
        for i in items
        if is_label_eligible(i) and i.get("order_item_id")
    ]


_PACKAGE_ID_KEYS = (
    "package_id",
    "PackageId",
    "packageId",
    "fulfillment_package_id",
    "FulfillmentPackageId",
)
_PACKAGE_NESTED_KEYS = ("package_info", "PackageInfo", "shipment", "fulfillment")


def _coerce_package_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def package_id_for_items(items: list[dict[str, Any]]) -> str | None:
    """First package_id on a label-eligible line item, if any."""
    for item in items:
        if not is_label_eligible(item):
            continue
        for key in _PACKAGE_ID_KEYS:
            package_id = _coerce_package_id(item.get(key))
            if package_id:
                return package_id
        for nest_key in _PACKAGE_NESTED_KEYS:
            nested = item.get(nest_key)
            if not isinstance(nested, dict):
                continue
            for key in _PACKAGE_ID_KEYS:
                package_id = _coerce_package_id(nested.get(key))
                if package_id:
                    return package_id
    return None


def order_label_meta(items: list[dict[str, Any]]) -> tuple[list[str], str | None]:
    return eligible_item_ids(items), package_id_for_items(items)
