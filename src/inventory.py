"""Workspace inventory — SKU-level read view over local product catalog."""

from __future__ import annotations

from typing import Any

from src.db.repo import get_repo

DEFAULT_LOW_STOCK = 5


def _sale_props_label(sale_props: Any) -> str:
    if not isinstance(sale_props, dict) or not sale_props:
        return ""
    parts = []
    for k, v in sale_props.items():
        if v is None or v == "":
            continue
        parts.append(f"{k}: {v}")
    return " / ".join(parts)


def _thumb(product: dict[str, Any], variant: dict[str, Any]) -> str | None:
    for source in (variant.get("images_json"), product.get("images_json")):
        if isinstance(source, list):
            for item in source:
                if isinstance(item, dict) and item.get("url"):
                    return str(item["url"])
                if isinstance(item, str) and item.startswith("http"):
                    return item
    return None


def stock_badge(quantity: int | None, *, low_stock_threshold: int = DEFAULT_LOW_STOCK) -> str:
    if quantity is None:
        return "Unknown"
    if quantity <= 0:
        return "Out of Stock"
    if quantity <= low_stock_threshold:
        return "Low Stock"
    return "In Stock"


def list_inventory(
    workspace_id: str,
    *,
    store_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    low_stock: bool = False,
    low_stock_threshold: int = DEFAULT_LOW_STOCK,
    sort: str = "product",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    repo = get_repo()
    if hasattr(repo, "list_inventory_skus"):
        return repo.list_inventory_skus(
            workspace_id,
            store_id=store_id,
            status=status,
            search=search,
            low_stock=low_stock,
            low_stock_threshold=low_stock_threshold,
            sort=sort,
            page=page,
            page_size=page_size,
        )
    # Fallback: assemble from products + variants (memory / incomplete repos).
    return _assemble_from_products(
        workspace_id,
        store_id=store_id,
        status=status,
        search=search,
        low_stock=low_stock,
        low_stock_threshold=low_stock_threshold,
        sort=sort,
        page=page,
        page_size=page_size,
    )


def inventory_summary(
    workspace_id: str,
    *,
    store_id: str | None = None,
    low_stock_threshold: int = DEFAULT_LOW_STOCK,
) -> dict[str, Any]:
    repo = get_repo()
    if hasattr(repo, "inventory_summary"):
        return repo.inventory_summary(
            workspace_id,
            store_id=store_id,
            low_stock_threshold=low_stock_threshold,
        )
    page = list_inventory(
        workspace_id,
        store_id=store_id,
        low_stock_threshold=low_stock_threshold,
        page=1,
        page_size=10_000,
    )
    items = page.get("items") or []
    product_ids = {str(i.get("product_id")) for i in items if i.get("product_id")}
    known = [i for i in items if i.get("quantity") is not None]
    return {
        "total_listings": len(product_ids),
        "total_skus": len(items),
        "active_listings": len(
            {
                str(i.get("product_id"))
                for i in items
                if str(i.get("listing_status") or "").lower() in {"active", "live"}
            }
        ),
        "known_low_stock_skus": sum(
            1
            for i in known
            if 0 < int(i["quantity"]) <= low_stock_threshold
        ),
        "out_of_stock_skus": sum(1 for i in known if int(i["quantity"]) <= 0),
        "unknown_quantity_skus": sum(1 for i in items if i.get("quantity") is None),
        "low_stock_threshold": low_stock_threshold,
    }


def _assemble_from_products(
    workspace_id: str,
    *,
    store_id: str | None,
    status: str | None,
    search: str | None,
    low_stock: bool,
    low_stock_threshold: int,
    sort: str,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    repo = get_repo()
    stores = {str(s["id"]): s for s in repo.list_stores(workspace_id)}
    slug_to_uuid = {
        str(s.get("store_id") or "").lower(): str(s["id"]) for s in stores.values()
    }
    store_uuid = None
    if store_id:
        store_uuid = store_id if store_id in stores else slug_to_uuid.get(store_id.lower())

    products = repo.list_daraz_products(
        workspace_id,
        {
            "store_uuids": [store_uuid] if store_uuid else None,
            "status": status,
            "search": search,
            "page": 1,
            "page_size": 200,
        },
    )
    rows: list[dict[str, Any]] = []
    for product in products.get("items") or []:
        variants = repo.list_daraz_product_variants(workspace_id, str(product["id"]))
        if not variants:
            continue
        store = stores.get(str(product.get("store_id"))) or {}
        for v in variants:
            qty = v.get("quantity")
            try:
                qty_n = int(qty) if qty is not None else None
            except (TypeError, ValueError):
                qty_n = None
            if low_stock:
                if qty_n is None or qty_n <= 0 or qty_n > low_stock_threshold:
                    continue
            title = product.get("title_en") or product.get("title") or "Product"
            needle = (search or "").strip().lower()
            if needle:
                blob = " ".join(
                    [
                        str(title),
                        str(v.get("seller_sku") or ""),
                        str(product.get("daraz_item_id") or ""),
                        _sale_props_label(v.get("sale_props_json")),
                    ]
                ).lower()
                if needle not in blob:
                    continue
            rows.append(
                {
                    "variant_id": v.get("id"),
                    "product_id": product.get("id"),
                    "store_id": store.get("store_id"),
                    "store_uuid": store.get("id"),
                    "store_display_name": store.get("display_name")
                    or store.get("store_name")
                    or store.get("store_id"),
                    "product_name": title,
                    "thumbnail_url": _thumb(product, v),
                    "seller_sku": v.get("seller_sku"),
                    "variant_label": _sale_props_label(v.get("sale_props_json")),
                    "price": v.get("price"),
                    "quantity": qty_n,
                    "stock_badge": stock_badge(
                        qty_n, low_stock_threshold=low_stock_threshold
                    ),
                    "listing_status": product.get("status_raw") or v.get("status_raw"),
                    "daraz_item_id": product.get("daraz_item_id"),
                    "daraz_sku_id": v.get("daraz_sku_id"),
                    "last_synced": v.get("synced_at")
                    or product.get("detail_synced_at")
                    or product.get("synced_at"),
                    "detail_complete": product.get("detail_complete"),
                }
            )

    sort_key = (sort or "product").lower()
    reverse = False
    if sort_key in {"quantity", "quantity_asc"}:
        rows.sort(key=lambda r: (r["quantity"] is None, r["quantity"] or 0))
    elif sort_key in {"quantity_desc"}:
        rows.sort(key=lambda r: (r["quantity"] is None, r["quantity"] or 0), reverse=True)
    elif sort_key in {"price", "price_asc"}:
        rows.sort(key=lambda r: (r["price"] is None, float(r["price"] or 0)))
    elif sort_key in {"price_desc"}:
        rows.sort(
            key=lambda r: (r["price"] is None, float(r["price"] or 0)), reverse=True
        )
    elif sort_key in {"last_synced", "last_synced_desc", "synced"}:
        rows.sort(key=lambda r: str(r.get("last_synced") or ""), reverse=True)
    else:
        rows.sort(key=lambda r: str(r.get("product_name") or "").lower())

    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "items": rows[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "low_stock_threshold": low_stock_threshold,
    }
