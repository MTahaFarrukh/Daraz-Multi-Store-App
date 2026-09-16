"""Sync Daraz seller catalog into local daraz_products / daraz_product_variants.

Performance strategy (408+ products/store)
------------------------------------------
1. Page ``/products/get`` with PAGE_SIZE=50 (options=1 includes images/skus).
2. List payload is rich enough for hub fields; call ``/product/item/get`` only when
   variation is empty OR video_ref missing OR options requested detail=True.
3. Detail fetches use a bounded ThreadPoolExecutor (DETAIL_CONCURRENCY=4) — never
   unbounded N+1 fan-out.
4. Per-store isolation: one store failure does not abort others.
5. Idempotent upserts on (store_id, daraz_item_id) / (store_id, daraz_sku_id).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from src.daraz_api import DarazApiError, DarazClient
from src.db.repo import get_repo
from src.ops import client_for_store
from src.token_refresh import refresh_one_store
from src.token_store import access_token_expires_soon

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
MAX_OFFSET = 20000
DETAIL_CONCURRENCY = 4


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _images_json(urls: Any) -> list[dict[str, Any]]:
    if not urls:
        return []
    if isinstance(urls, str):
        urls = [urls]
    out = []
    for i, u in enumerate(urls):
        if not u:
            continue
        out.append({"url": str(u), "position": i, "kind": "product"})
    return out


def _sku_images_json(urls: Any) -> list[dict[str, Any]]:
    rows = _images_json(urls)
    for r in rows:
        r["kind"] = "variant"
    return rows


def product_payload_from_daraz(
    workspace_id: str,
    store_uuid: str,
    product: dict[str, Any],
    *,
    category_name: str | None = None,
) -> dict[str, Any]:
    attrs = product.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    skus = product.get("skus") or []
    product_url = None
    if isinstance(skus, list) and skus:
        product_url = skus[0].get("Url") or skus[0].get("url")
    video = attrs.get("video")
    return {
        "workspace_id": workspace_id,
        "store_id": store_uuid,
        "daraz_item_id": str(product.get("item_id") or ""),
        "title": attrs.get("name") or attrs.get("name_en") or product.get("name"),
        "title_en": attrs.get("name_en"),
        "primary_category_id": _int(product.get("primary_category")),
        "primary_category_name": category_name,
        "brand": attrs.get("brand"),
        "description": attrs.get("description"),
        "description_en": attrs.get("description_en"),
        "short_description": attrs.get("short_description"),
        "short_description_en": attrs.get("short_description_en"),
        "package_content": attrs.get("package_content")
        or attrs.get("package_content_en"),
        "status_raw": str(product.get("status") or "") or None,
        "product_url": product_url,
        "attributes_json": attrs,
        "variation_json": product.get("variation") or {},
        "images_json": _images_json(product.get("images")),
        "market_images_json": _images_json(product.get("marketImages")),
        "video_ref": str(video) if video not in (None, "") else None,
        "raw_json": {
            "item_id": product.get("item_id"),
            "primary_category": product.get("primary_category"),
            "status": product.get("status"),
            "sku_count": len(skus) if isinstance(skus, list) else 0,
        },
    }


def variant_payloads_from_daraz(
    workspace_id: str,
    store_uuid: str,
    product_uuid: str,
    product: dict[str, Any],
) -> list[dict[str, Any]]:
    skus = product.get("skus") or []
    if not isinstance(skus, list):
        return []
    rows: list[dict[str, Any]] = []
    for sku in skus:
        sku_id = sku.get("SkuId") or sku.get("sku_id")
        if sku_id is None or sku_id == "":
            continue
        sale = sku.get("saleProp") or sku.get("sale_prop") or {}
        if not isinstance(sale, dict):
            sale = {}
        rows.append(
            {
                "workspace_id": workspace_id,
                "store_id": store_uuid,
                "product_id": product_uuid,
                "daraz_sku_id": str(sku_id),
                "seller_sku": sku.get("SellerSku") or sku.get("seller_sku"),
                "shop_sku": sku.get("ShopSku") or sku.get("shop_sku"),
                "sale_props_json": sale,
                "price": _num(sku.get("price")),
                "special_price": _num(sku.get("special_price")),
                "quantity": _int(sku.get("quantity") if sku.get("quantity") is not None else sku.get("Available")),
                "package_weight": _num(sku.get("package_weight")),
                "package_length": _num(sku.get("package_length")),
                "package_width": _num(sku.get("package_width")),
                "package_height": _num(sku.get("package_height")),
                "images_json": _sku_images_json(sku.get("Images") or sku.get("images")),
                "status_raw": sku.get("Status") or sku.get("status"),
            }
        )
    return rows


def _needs_detail(product: dict[str, Any]) -> bool:
    variation = product.get("variation")
    attrs = product.get("attributes") or {}
    has_variation = isinstance(variation, dict) and bool(variation)
    has_video = bool(attrs.get("video")) if isinstance(attrs, dict) else False
    # Always detail when multi-sku to capture variation definition reliably
    skus = product.get("skus") or []
    multi = isinstance(skus, list) and len(skus) > 1
    return multi or not has_variation or not has_video


def sync_store_products(
    workspace_id: str,
    store: dict[str, Any],
    *,
    fetch_details: bool = True,
    max_products: int | None = None,
) -> dict[str, Any]:
    repo = get_repo()
    store_uuid = str(store.get("id") or "")
    slug = str(store.get("store_id") or "")
    if not store_uuid:
        return {
            "store_id": slug,
            "store_uuid": None,
            "sync_status": "error",
            "sync_error": "missing_internal_store_id",
            "products_upserted": 0,
            "variants_upserted": 0,
        }

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

        products_upserted = 0
        variants_upserted = 0
        detail_ok = 0
        detail_fail = 0
        warning: str | None = None
        offset = 0
        seen_items: set[str] = set()
        page_products: list[dict[str, Any]] = []

        while offset <= MAX_OFFSET:
            resp = client.get_products(
                filter="all", offset=offset, limit=PAGE_SIZE, options="1"
            )
            data = resp.get("data") or {}
            batch = data.get("products") or []
            if not isinstance(batch, list) or not batch:
                break
            for p in batch:
                iid = str(p.get("item_id") or "")
                if not iid:
                    continue
                if iid in seen_items:
                    warning = "pagination_overlap_detected"
                    break
                seen_items.add(iid)
                page_products.append(p)
                if max_products and len(page_products) >= max_products:
                    break
            if warning == "pagination_overlap_detected":
                break
            if max_products and len(page_products) >= max_products:
                break
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
            if offset > MAX_OFFSET:
                warning = "pagination_truncated_offset_limit"
                break

        # Optional bounded detail enrichment
        details_by_id: dict[str, dict[str, Any]] = {}
        if fetch_details:
            need = [p for p in page_products if _needs_detail(p)]
            if need:
                def _fetch(item_id: str) -> tuple[str, dict[str, Any] | None, str | None]:
                    try:
                        d = client.get_product_item(item_id)
                        return item_id, d.get("data") or d, None
                    except Exception as exc:  # noqa: BLE001
                        return item_id, None, str(exc)[:160]

                with ThreadPoolExecutor(max_workers=DETAIL_CONCURRENCY) as pool:
                    futs = [
                        pool.submit(_fetch, str(p.get("item_id")))
                        for p in need
                        if p.get("item_id") is not None
                    ]
                    for fut in as_completed(futs):
                        iid, detail, err = fut.result()
                        if detail:
                            details_by_id[iid] = detail
                            detail_ok += 1
                        else:
                            detail_fail += 1
                            logger.info("product detail failed item=%s err=%s", iid, err)

        for listed in page_products:
            iid = str(listed.get("item_id") or "")
            merged = dict(listed)
            if iid in details_by_id:
                detail = details_by_id[iid]
                # Prefer detail fields when present
                for key in (
                    "attributes",
                    "variation",
                    "images",
                    "marketImages",
                    "skus",
                    "status",
                    "primary_category",
                ):
                    if detail.get(key) is not None:
                        merged[key] = detail[key]
            payload = product_payload_from_daraz(workspace_id, store_uuid, merged)
            if not payload["daraz_item_id"]:
                continue
            row = repo.upsert_daraz_product(payload)
            products_upserted += 1
            variants = variant_payloads_from_daraz(
                workspace_id, store_uuid, str(row["id"]), merged
            )
            repo.replace_product_variants(workspace_id, str(row["id"]), variants)
            variants_upserted += len(variants)

        return {
            "store_id": slug,
            "store_uuid": store_uuid,
            "sync_status": "partial" if warning or detail_fail else "ok",
            "sync_error": warning,
            "products_upserted": products_upserted,
            "variants_upserted": variants_upserted,
            "detail_fetched": detail_ok,
            "detail_failed": detail_fail,
            "unique_items_seen": len(seen_items),
            "synced_at": _now_iso(),
        }
    except DarazApiError as exc:
        logger.warning("Product sync Daraz error store=%s: %s", slug, exc)
        return {
            "store_id": slug,
            "store_uuid": store_uuid,
            "sync_status": "error",
            "sync_error": f"daraz:{exc.code}:{str(exc)[:160]}",
            "products_upserted": 0,
            "variants_upserted": 0,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Product sync failed store=%s", slug)
        return {
            "store_id": slug,
            "store_uuid": store_uuid,
            "sync_status": "error",
            "sync_error": f"{type(exc).__name__}:{str(exc)[:160]}",
            "products_upserted": 0,
            "variants_upserted": 0,
        }


def sync_workspace_products(
    workspace_id: str,
    *,
    store_ids: list[str] | None = None,
    fetch_details: bool = True,
) -> dict[str, Any]:
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

    results = [
        sync_store_products(workspace_id, store, fetch_details=fetch_details)
        for store in stores
    ]
    ok = sum(1 for r in results if r.get("sync_status") == "ok")
    return {
        "stores": len(results),
        "ok": ok,
        "failed": len(results) - ok,
        "results": results,
    }
