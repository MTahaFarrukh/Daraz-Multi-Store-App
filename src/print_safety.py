"""Print-target validation and label-print event recording (order-scoped)."""

from __future__ import annotations

from typing import Any

from src.db.repo import get_repo
from src.orders import eligible_item_ids, is_label_eligible, package_id_for_items


def _item_as_eligibility_dict(item: dict[str, Any]) -> dict[str, Any]:
    """Shape stored items for is_label_eligible / eligible_item_ids."""
    return {
        "order_item_id": item.get("daraz_order_item_id") or item.get("order_item_id"),
        "status": item.get("status_raw") or item.get("status") or "",
        "package_id": item.get("package_id"),
    }


def order_is_eligible(order: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    """Eligible if status_group is ready_to_ship OR any item is label-eligible."""
    if str(order.get("status_group") or "") == "ready_to_ship":
        return True
    shaped = [_item_as_eligibility_dict(i) for i in items]
    return any(is_label_eligible(i) for i in shaped)


def printable_meta(
    order: dict[str, Any], items: list[dict[str, Any]]
) -> dict[str, Any]:
    shaped = [_item_as_eligibility_dict(i) for i in items]
    item_ids = eligible_item_ids(shaped)
    # If order-level ready_to_ship but no item statuses qualify, include all item ids
    if not item_ids and str(order.get("status_group") or "") == "ready_to_ship":
        item_ids = [
            str(i.get("daraz_order_item_id") or i.get("order_item_id"))
            for i in items
            if i.get("daraz_order_item_id") or i.get("order_item_id")
        ]
    package_id = package_id_for_items(shaped)
    if not package_id:
        for i in items:
            if i.get("package_id"):
                package_id = str(i["package_id"])
                break
    return {
        "order_item_ids": item_ids,
        "package_id": package_id,
    }


def validate_print_targets(
    workspace_id: str,
    order_uuids: list[str],
) -> dict[str, Any]:
    """Classify orders into new_printable / already_printed / not_eligible / errors."""
    repo = get_repo()
    new_printable: list[dict[str, Any]] = []
    already_printed: list[dict[str, Any]] = []
    not_eligible: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    seen: set[str] = set()
    for raw_id in order_uuids:
        oid = str(raw_id)
        if oid in seen:
            continue
        seen.add(oid)
        order = repo.get_order_by_id(workspace_id, oid)
        if not order:
            errors.append({"order_id": oid, "error": "not_found"})
            continue
        items = repo.list_order_items(workspace_id, oid)
        eligible = order_is_eligible(order, items)
        meta = printable_meta(order, items)
        entry = {
            "order_id": oid,
            "store_id": str(order.get("store_id")),
            "daraz_order_id": str(order.get("daraz_order_id")),
            "status_group": order.get("status_group"),
            "order_item_ids": meta["order_item_ids"],
            "package_id": meta["package_id"],
        }
        if not eligible or not meta["order_item_ids"]:
            not_eligible.append({**entry, "reason": "not_eligible"})
            continue
        printed = repo.has_label_print(
            workspace_id, str(order["store_id"]), str(order["daraz_order_id"])
        )
        if printed:
            already_printed.append(entry)
        else:
            new_printable.append(entry)

    return {
        "new_printable": new_printable,
        "already_printed": already_printed,
        "not_eligible": not_eligible,
        "errors": errors,
    }


def record_label_prints(
    workspace_id: str,
    user_id: str | None,
    print_job_id: str | None,
    successes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Insert order_label_prints rows after a successful PDF for each order.

    Each success dict needs:
      order_id, store_id, daraz_order_id, order_item_ids, package_id?,
      is_reprint?, fetch_source?
    """
    repo = get_repo()
    inserted: list[dict[str, Any]] = []
    for s in successes:
        row = repo.insert_label_print(
            {
                "workspace_id": workspace_id,
                "store_id": s["store_id"],
                "order_id": s["order_id"],
                "daraz_order_id": s["daraz_order_id"],
                "package_id": s.get("package_id"),
                "order_item_ids": s.get("order_item_ids") or [],
                "print_job_id": print_job_id,
                "printed_by_user_id": user_id,
                "is_reprint": bool(s.get("is_reprint")),
                "fetch_source": s.get("fetch_source"),
            }
        )
        inserted.append(row)
    return inserted
