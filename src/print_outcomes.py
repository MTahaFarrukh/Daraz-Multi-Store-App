"""Print target outcomes — every selected order must have an explicit result."""

from __future__ import annotations

from typing import Any

# Canonical outcome states (Phase 4D)
SUCCESS = "SUCCESS"
ALREADY_PRINTED = "ALREADY_PRINTED"  # blocked by duplicate protection (not printed this job)
NOT_ELIGIBLE = "NOT_ELIGIBLE"
PACKAGE_RESOLUTION_FAILED = "PACKAGE_RESOLUTION_FAILED"
DOCUMENT_FAILED = "DOCUMENT_FAILED"
DARAZ_ERROR = "DARAZ_ERROR"
MERGE_FAILED = "MERGE_FAILED"
UNKNOWN_FAILURE = "UNKNOWN_FAILURE"
NOT_FOUND = "NOT_FOUND"
STORE_NOT_FOUND = "STORE_NOT_FOUND"


def make_outcome(
    *,
    order_id: str,
    state: str,
    reason: str | None = None,
    daraz_order_id: str | None = None,
    order_number: str | None = None,
    store_id: str | None = None,
    store_slug: str | None = None,
    store_display_name: str | None = None,
    package_id: str | None = None,
    order_item_ids: list[str] | None = None,
    daraz_code: str | None = None,
    daraz_message: str | None = None,
    is_reprint: bool = False,
) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "daraz_order_id": daraz_order_id,
        "order_number": order_number,
        "store_id": store_id,
        "store_slug": store_slug,
        "store_display_name": store_display_name,
        "package_id": package_id,
        "order_item_ids": list(order_item_ids or []),
        "state": state,
        "reason": reason,
        "daraz_code": daraz_code,
        "daraz_message": daraz_message,
        "is_reprint": is_reprint,
        "success": state == SUCCESS,
    }


def summarize_outcomes(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    selected = len(outcomes)
    success = sum(1 for o in outcomes if o.get("state") == SUCCESS)
    failed = selected - success
    by_state: dict[str, int] = {}
    for o in outcomes:
        st = str(o.get("state") or UNKNOWN_FAILURE)
        by_state[st] = by_state.get(st, 0) + 1
    failed_ids = [str(o["order_id"]) for o in outcomes if o.get("state") != SUCCESS]
    if selected == 0:
        status = "empty"
    elif failed == 0:
        status = "success"
    elif success == 0:
        status = "failed"
    else:
        status = "partial_success"
    return {
        "selected_count": selected,
        "success_count": success,
        "failed_count": failed,
        "partial": status == "partial_success",
        "print_status": status,
        "by_state": by_state,
        "failed_order_ids": failed_ids,
        "message": f"Print completed: {success} / {selected}",
    }
