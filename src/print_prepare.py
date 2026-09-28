"""Canonical prepared print targets — hydrate once, validate from prepared data."""

from __future__ import annotations

import time
from typing import Any

from src.db.repo import get_repo
from src.print_hydrate import hydrate_missing_order_items
from src.print_safety import order_is_eligible, printable_meta


def prepare_print_targets(
    workspace_id: str,
    order_uuids: list[str],
    *,
    hydrate: bool = True,
) -> dict[str, Any]:
    """Build one prepared structure per selected target (hydrate at most once).

    Each prepared target contains:
      order, items, package meta, print events / printed flag, eligibility,
      store (if available), target identity fields.
    """
    t0 = time.perf_counter()
    selected: list[str] = []
    seen: set[str] = set()
    for raw_id in order_uuids:
        oid = str(raw_id)
        if oid in seen:
            continue
        seen.add(oid)
        selected.append(oid)

    hydrate_report: dict[str, Any] = {
        "hydrate_ms": 0,
        "orders_hydrated": 0,
        "api_calls": 0,
        "orders_missing": 0,
    }
    if hydrate and selected:
        hydrate_report = hydrate_missing_order_items(workspace_id, selected)

    t_batch = time.perf_counter()
    repo = get_repo()
    orders_by_id: dict[str, dict[str, Any]] = {}
    if hasattr(repo, "list_orders_by_ids"):
        orders_by_id = repo.list_orders_by_ids(workspace_id, selected)
    else:
        for oid in selected:
            order = repo.get_order_by_id(workspace_id, oid)
            if order:
                orders_by_id[oid] = order

    items_by_order: dict[str, list[dict[str, Any]]] = {
        oid: [] for oid in selected
    }
    if hasattr(repo, "list_order_items_by_order_ids"):
        items_by_order = repo.list_order_items_by_order_ids(workspace_id, selected)
    else:
        for oid in selected:
            items_by_order[oid] = repo.list_order_items(workspace_id, oid)

    prints_by_order = repo.list_label_prints_for_orders(workspace_id, selected)

    store_uuids = sorted(
        {
            str(o.get("store_id"))
            for o in orders_by_id.values()
            if o.get("store_id")
        }
    )
    stores_by_id: dict[str, dict[str, Any]] = {}
    if store_uuids and hasattr(repo, "list_stores_by_uuids"):
        stores_by_id = repo.list_stores_by_uuids(workspace_id, store_uuids)
    else:
        for su in store_uuids:
            store = repo.get_store_by_uuid(workspace_id, su)
            if store:
                stores_by_id[su] = store

    batch_read_ms = int((time.perf_counter() - t_batch) * 1000)

    prepared: list[dict[str, Any]] = []
    for oid in selected:
        order = orders_by_id.get(oid)
        if not order:
            prepared.append(
                {
                    "order_id": oid,
                    "order": None,
                    "items": [],
                    "store": None,
                    "store_id": None,
                    "daraz_order_id": None,
                    "package_id": None,
                    "order_item_ids": [],
                    "eligible": False,
                    "printed": False,
                    "print_events": [],
                    "missing": True,
                }
            )
            continue
        items = list(items_by_order.get(oid) or [])
        meta = printable_meta(order, items)
        store_uuid = str(order.get("store_id") or "")
        store = stores_by_id.get(store_uuid)
        events = list(prints_by_order.get(oid) or [])
        printed = bool(events) or repo.has_label_print(
            workspace_id, store_uuid, str(order.get("daraz_order_id") or "")
        )
        prepared.append(
            {
                "order_id": oid,
                "order": order,
                "items": items,
                "store": store,
                "store_id": store_uuid or None,
                "daraz_order_id": str(order.get("daraz_order_id") or "") or None,
                "package_id": meta.get("package_id"),
                "order_item_ids": list(meta.get("order_item_ids") or []),
                "eligible": order_is_eligible(order, items),
                "printed": printed,
                "print_events": events,
                "missing": False,
                "status_group": order.get("status_group"),
            }
        )

    prepare_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "selected": selected,
        "targets": prepared,
        "hydrate": hydrate_report,
        "timings_ms": {
            "prepare_ms": prepare_ms,
            "hydrate_ms": int(hydrate_report.get("hydrate_ms") or 0),
            "batch_read_ms": batch_read_ms,
        },
    }


def classify_prepared_targets(prepared: dict[str, Any]) -> dict[str, Any]:
    """Classify prepared targets into printable buckets (no DB / hydrate)."""
    new_printable: list[dict[str, Any]] = []
    already_printed: list[dict[str, Any]] = []
    not_eligible: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    unprinted_ids: list[str] = []
    printed_ids: list[str] = []

    for t in prepared.get("targets") or []:
        oid = str(t["order_id"])
        if t.get("missing"):
            errors.append({"order_id": oid, "error": "not_found"})
            continue
        entry = {
            "order_id": oid,
            "store_id": str(t.get("store_id") or ""),
            "daraz_order_id": str(t.get("daraz_order_id") or ""),
            "status_group": t.get("status_group"),
            "order_item_ids": list(t.get("order_item_ids") or []),
            "package_id": t.get("package_id"),
        }
        if t.get("printed"):
            printed_ids.append(oid)
        else:
            unprinted_ids.append(oid)

        if not t.get("eligible"):
            not_eligible.append({**entry, "reason": "not_eligible"})
            continue
        if not entry["order_item_ids"]:
            not_eligible.append({**entry, "reason": "no_order_item_ids"})
            continue
        if t.get("printed"):
            already_printed.append(entry)
        else:
            new_printable.append(entry)

    return {
        "new_printable": new_printable,
        "already_printed": already_printed,
        "not_eligible": not_eligible,
        "errors": errors,
        "unprinted_ids": unprinted_ids,
        "printed_ids": printed_ids,
        "selected_count": len(prepared.get("selected") or []),
        "hydrate": prepared.get("hydrate") or {},
        "prepared": prepared,
    }
