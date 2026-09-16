"""Hydrate missing Daraz order items before print validation."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.db.repo import get_repo
from src.order_sync import item_payload_from_daraz
from src.orders import extract_order_items

logger = logging.getLogger(__name__)


def _fetch_items_for_orders_counted(
    client: Any,
    order_ids: list[str],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Return (daraz_order_id → raw items, api_call_count)."""
    by_order: dict[str, list[dict[str, Any]]] = {oid: [] for oid in order_ids}
    api_calls = 0
    for i in range(0, len(order_ids), 50):
        chunk = order_ids[i : i + 50]
        parsed = False
        try:
            api_calls += 1
            multi = client.get_multiple_order_items(chunk)
            data = multi.get("data")
            if isinstance(data, list):
                for entry in data:
                    if not isinstance(entry, dict):
                        continue
                    oid = str(entry.get("order_id") or "")
                    entries = (
                        entry.get("order_items")
                        or entry.get("orderItems")
                        or entry.get("items")
                        or []
                    )
                    if oid and isinstance(entries, list):
                        by_order[oid] = [e for e in entries if isinstance(e, dict)]
                        parsed = True
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_multiple_order_items failed: %s", exc)
            parsed = False

        if not parsed:
            for oid in chunk:
                try:
                    api_calls += 1
                    items_resp = client.get_order_items(oid)
                    by_order[oid] = extract_order_items(items_resp)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("get_order_items failed order=%s: %s", oid, exc)
                    by_order[oid] = []
    return by_order, api_calls


def hydrate_missing_order_items(
    workspace_id: str,
    order_uuids: list[str],
) -> dict[str, Any]:
    """Fetch and upsert order items for selected orders that have none in DB.

    Load-RTS often upserts order headers only; print validation needs items.
    """
    from src.ops import client_for_store

    t0 = time.perf_counter()
    repo = get_repo()
    api_calls = 0
    orders_hydrated = 0

    missing: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_id in order_uuids:
        oid = str(raw_id)
        if oid in seen:
            continue
        seen.add(oid)
        order = repo.get_order_by_id(workspace_id, oid)
        if not order:
            continue
        items = repo.list_order_items(workspace_id, oid)
        if not items:
            missing.append(order)

    by_store: dict[str, list[dict[str, Any]]] = {}
    for order in missing:
        by_store.setdefault(str(order.get("store_id")), []).append(order)

    for store_uuid, orders in by_store.items():
        store = repo.get_store_by_uuid(workspace_id, store_uuid)
        if not store:
            continue
        try:
            client = client_for_store(store)
        except Exception as exc:  # noqa: BLE001
            logger.warning("hydrate: client failed store=%s: %s", store_uuid, exc)
            continue

        daraz_ids = [str(o.get("daraz_order_id") or "") for o in orders]
        daraz_ids = [d for d in daraz_ids if d]
        if not daraz_ids:
            continue

        items_map, calls = _fetch_items_for_orders_counted(client, daraz_ids)
        api_calls += calls
        uuid_by_daraz = {str(o["daraz_order_id"]): str(o["id"]) for o in orders}

        for daraz_oid, items in items_map.items():
            order_uuid = uuid_by_daraz.get(daraz_oid)
            if not order_uuid or not items:
                continue
            wrote = False
            for item in items:
                payload = item_payload_from_daraz(
                    workspace_id, store_uuid, order_uuid, daraz_oid, item
                )
                if not payload.get("daraz_order_item_id"):
                    continue
                repo.upsert_daraz_order_item(payload)
                wrote = True
            if wrote:
                orders_hydrated += 1

    hydrate_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "hydrate_ms": hydrate_ms,
        "orders_hydrated": orders_hydrated,
        "api_calls": api_calls,
        "orders_missing": len(missing),
    }
