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
    *,
    skip_hydrate: bool = False,
    prepared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify orders into printable buckets; expose print-event partitions.

    Prefer passing a canonical ``prepared`` structure from
    ``prepare_print_targets`` so hydrate runs at most once. When ``prepared``
    is provided, classification uses that data with no further DB hydrate.
    """
    if prepared is not None:
        from src.print_prepare import classify_prepared_targets

        return classify_prepared_targets(prepared)

    from src.print_prepare import classify_prepared_targets, prepare_print_targets

    prepared_data = prepare_print_targets(
        workspace_id, order_uuids, hydrate=not skip_hydrate
    )
    return classify_prepared_targets(prepared_data)


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

    Uses a single atomic transaction when available. Callers must treat a raised
    exception as print-history persistence failure (no partial history).
    """
    repo = get_repo()
    payloads = [
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
        for s in successes]
    if not payloads:
        return []
    if hasattr(repo, "insert_label_prints_atomic"):
        return repo.insert_label_prints_atomic(payloads)
    return [repo.insert_label_print(p) for p in payloads]
