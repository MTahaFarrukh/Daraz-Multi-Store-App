"""Sync Daraz orders into local `daraz_orders` / `daraz_order_items`.

Default windows
---------------
- Default Sync (no window args): ``created_after = now - 30 days`` so RTS and
  other open orders that have not been *updated* recently are still pulled.
  A 7-day ``update_after`` default missed whole stores' stale ready_to_ship rows.
- Incremental: pass ``update_after`` explicitly (e.g. now - 7 days) to refresh
  only recently changed orders.
- Broader backfill: pass ``days`` (sets ``created_after = now - days``) or an
  explicit ``created_after``.

Pagination uses PAGE_SIZE=100 and MAX_OFFSET=5000 with overlap detection
similar to ``performance_sync``. Finance APIs are never contacted.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.daraz_api import DarazApiError, DarazClient
from src.db.repo import get_repo
from src.ops import client_for_store
from src.order_status import map_status_group, status_raw_text
from src.orders import extract_order_items, extract_orders, package_id_for_items
from src.token_refresh import refresh_one_store
from src.token_store import access_token_expires_soon

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
MAX_OFFSET = 5000
DEFAULT_UPDATE_DAYS = 7
DEFAULT_CREATED_DAYS = 30


def _iso_ago(days: int) -> str:
    dt = datetime.now(UTC) - timedelta(days=days)
    return dt.astimezone().replace(microsecond=0).isoformat()


def _parse_money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ts(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def order_payload_from_daraz(
    workspace_id: str,
    store_uuid: str,
    order: dict[str, Any],
) -> dict[str, Any]:
    statuses = order.get("statuses")
    if statuses is None:
        statuses = order.get("status")
    shipping = order.get("address_shipping")
    shipping_dict = shipping if isinstance(shipping, dict) else {}
    first = order.get("customer_first_name") or shipping_dict.get("first_name")
    last = order.get("customer_last_name") or shipping_dict.get("last_name")
    return {
        "workspace_id": workspace_id,
        "store_id": store_uuid,
        "daraz_order_id": str(order.get("order_id") or ""),
        "order_number": str(order.get("order_number") or "") or None,
        "status_raw": status_raw_text(statuses),
        "status_group": map_status_group(statuses),
        "statuses": statuses if statuses is not None else None,
        "created_at_daraz": _ts(order.get("created_at") or order.get("created_at_daraz")),
        "updated_at_daraz": _ts(order.get("updated_at") or order.get("updated_at_daraz")),
        "price": _parse_money(order.get("price")),
        "currency": order.get("currency") or order.get("price_currency") or "PKR",
        "items_count": _parse_int(order.get("items_count"), 0),
        "customer_first_name": first,
        "customer_last_name": last,
        "address_shipping": order.get("address_shipping"),
        "address_billing": order.get("address_billing"),
        "payment_method": order.get("payment_method"),
        "shipping_fee": _parse_money(order.get("shipping_fee")),
        "warehouse_code": order.get("warehouse_code"),
    }


def item_payload_from_daraz(
    workspace_id: str,
    store_uuid: str,
    order_uuid: str,
    daraz_order_id: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    package_id = None
    for key in ("package_id", "PackageId", "packageId"):
        if item.get(key):
            package_id = str(item.get(key)).strip() or None
            break
    if not package_id:
        package_id = package_id_for_items([item])
    return {
        "workspace_id": workspace_id,
        "store_id": store_uuid,
        "order_id": order_uuid,
        "daraz_order_item_id": str(item.get("order_item_id") or item.get("orderItemId") or ""),
        "daraz_order_id": daraz_order_id,
        "status_raw": status_raw_text(item.get("status") or item.get("Status")),
        "package_id": package_id,
        "name": item.get("name") or item.get("product_name"),
        "sku": item.get("sku") or item.get("SellerSku"),
        "sku_id": str(item.get("sku_id") or item.get("SkuId") or "") or None,
        "product_id": str(item.get("product_id") or item.get("productId") or "") or None,
        "quantity": _parse_int(item.get("quantity") or item.get("qty"), 1) or 1,
        "item_price": _parse_money(item.get("item_price") or item.get("ItemPrice")),
        "paid_price": _parse_money(item.get("paid_price") or item.get("PaidPrice")),
        "currency": item.get("currency") or "PKR",
        "tracking_code": item.get("tracking_code") or item.get("TrackingCode"),
        "shipment_provider": item.get("shipment_provider") or item.get("ShipmentProvider"),
        "shipping_type": item.get("shipping_type") or item.get("ShippingType"),
        "warehouse_code": item.get("warehouse_code") or item.get("WarehouseCode"),
    }


def _fetch_items_for_orders(
    client: DarazClient,
    order_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Return daraz_order_id → list of raw item dicts."""
    by_order: dict[str, list[dict[str, Any]]] = {oid: [] for oid in order_ids}
    for i in range(0, len(order_ids), 50):
        chunk = order_ids[i : i + 50]
        parsed = False
        try:
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
                    items_resp = client.get_order_items(oid)
                    by_order[oid] = extract_order_items(items_resp)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("get_order_items failed order=%s: %s", oid, exc)
                    by_order[oid] = []
    return by_order


def sync_store_orders(
    workspace_id: str,
    store: dict[str, Any],
    *,
    update_after: str | None = None,
    created_after: str | None = None,
    status: str = "all",
    include_items: bool = True,
) -> dict[str, Any]:
    """Sync one store's orders into the local DB. Partial-safe (returns status)."""
    repo = get_repo()
    store_uuid = str(store.get("id") or "")
    slug = str(store.get("store_id") or "")
    if not store_uuid:
        return {
            "store_uuid": None,
            "store_id": slug,
            "sync_status": "error",
            "sync_error": "missing_internal_store_id",
            "orders_upserted": 0,
            "items_upserted": 0,
        }

    if not update_after and not created_after:
        # Prefer created_after so multi-store RTS that sat untouched still sync.
        created_after = _iso_ago(DEFAULT_CREATED_DAYS)

    try:
        try:

            def _upsert(record: dict[str, Any]) -> dict[str, Any]:
                return repo.upsert_store(workspace_id, record)

            if access_token_expires_soon(store, within_minutes=60):
                refresh_one_store(store, upsert_fn=_upsert)
            refreshed = repo.get_store(workspace_id, slug) or store
        except Exception as exc:  # noqa: BLE001
            logger.info("Token refresh skipped for %s: %s", slug, exc)
            refreshed = store

        client = client_for_store(refreshed)
        client.timeout = 60.0

        seen: set[str] = set()
        offset = 0
        orders_upserted = 0
        items_upserted = 0
        warning: str | None = None

        while offset <= MAX_OFFSET:
            resp = client.get_orders(
                update_after=update_after,
                created_after=created_after,
                status=status,
                limit=PAGE_SIZE,
                offset=offset,
                sort_by="updated_at" if update_after else "created_at",
                sort_direction="ASC",
            )
            orders = extract_orders(resp)
            if not orders:
                break

            page_ids: list[str] = []
            for order in orders:
                oid = str(order.get("order_id") or "")
                page_ids.append(oid)
                if oid and oid in seen:
                    warning = "pagination_overlap_detected"
                    break
                if oid:
                    seen.add(oid)
            if warning == "pagination_overlap_detected":
                break

            # Upsert order headers
            order_rows: list[dict[str, Any]] = []
            for order in orders:
                oid = str(order.get("order_id") or "")
                if not oid:
                    continue
                payload = order_payload_from_daraz(workspace_id, store_uuid, order)
                if not payload["daraz_order_id"]:
                    continue
                row = repo.upsert_daraz_order(payload)
                order_rows.append(row)
                orders_upserted += 1

            if include_items and order_rows:
                oid_list = [str(r["daraz_order_id"]) for r in order_rows]
                items_map = _fetch_items_for_orders(client, oid_list)
                uuid_by_daraz = {str(r["daraz_order_id"]): str(r["id"]) for r in order_rows}
                for daraz_oid, items in items_map.items():
                    order_uuid = uuid_by_daraz.get(daraz_oid)
                    if not order_uuid:
                        continue
                    for item in items:
                        ip = item_payload_from_daraz(
                            workspace_id, store_uuid, order_uuid, daraz_oid, item
                        )
                        if not ip["daraz_order_item_id"]:
                            continue
                        repo.upsert_daraz_order_item(ip)
                        items_upserted += 1

            if len(orders) < PAGE_SIZE:
                break
            if len(set(page_ids)) < len(page_ids):
                warning = "duplicate_ids_in_page"
                break
            offset += PAGE_SIZE
            if offset > MAX_OFFSET:
                warning = "pagination_truncated_offset_limit"
                break

        return {
            "store_uuid": store_uuid,
            "store_id": slug,
            "sync_status": "partial" if warning else "ok",
            "sync_error": warning,
            "orders_upserted": orders_upserted,
            "items_upserted": items_upserted,
            "unique_orders_seen": len(seen),
        }
    except DarazApiError as exc:
        logger.warning("Order sync Daraz error store=%s: %s", slug, exc)
        return {
            "store_uuid": store_uuid,
            "store_id": slug,
            "sync_status": "error",
            "sync_error": f"daraz:{exc.code}:{str(exc)[:160]}",
            "orders_upserted": 0,
            "items_upserted": 0,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Order sync failed store=%s", slug)
        return {
            "store_uuid": store_uuid,
            "store_id": slug,
            "sync_status": "error",
            "sync_error": f"{type(exc).__name__}:{str(exc)[:160]}",
            "orders_upserted": 0,
            "items_upserted": 0,
        }


def sync_workspace_orders(
    workspace_id: str,
    *,
    store_ids: list[str] | None = None,
    update_after: str | None = None,
    created_after: str | None = None,
    days: int | None = None,
    status: str = "all",
    include_items: bool = True,
) -> dict[str, Any]:
    """Sync orders for selected stores (or all). Partial failure per store is OK.

    When ``days`` is set and no explicit window is provided, uses
    ``created_after = now - days`` for a broader initial pull.
    """
    repo = get_repo()
    all_stores = repo.list_stores(workspace_id)
    if store_ids is not None:
        if not store_ids:
            raise ValueError("No stores selected. Pick at least one store.")
        selected: list[dict[str, Any]] = []
        for sid in store_ids:
            store = repo.get_store(workspace_id, sid) or repo.get_store_by_uuid(
                workspace_id, sid
            )
            if not store:
                raise ValueError(f"Unknown store: {sid}")
            selected.append(store)
        stores = selected
    else:
        stores = all_stores

    if days is not None and not update_after and not created_after:
        created_after = _iso_ago(int(days))

    results = []
    for store in stores:
        results.append(
            sync_store_orders(
                workspace_id,
                store,
                update_after=update_after,
                created_after=created_after,
                status=status,
                include_items=include_items,
            )
        )
    ok = sum(1 for r in results if r.get("sync_status") == "ok")
    return {
        "stores": len(results),
        "ok": ok,
        "failed": len(results) - ok,
        "results": results,
    }
