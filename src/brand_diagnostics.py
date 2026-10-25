"""Read-only category brand diagnostics — never CreateProduct or image migrate."""

from __future__ import annotations

from collections import Counter
from typing import Any

from src.brand_resolve import (
    clear_no_brand_cache_for_tests,
    is_no_brand_label,
    resolve_destination_no_brand,
)
from src.daraz_api import DarazApiError
from src.db.repo import get_repo


def public_lookup_fields(
    lookup: dict[str, Any] | None, *, cache_hit: bool | None = None
) -> dict[str, Any]:
    """Expose resolver lookup diagnostics for API/UI (never tokens/secrets)."""
    src = lookup or {}
    out: dict[str, Any] = {}
    for key in (
        "strategy",
        "pages",
        "name_filter_effective",
        "complete",
        "stop_reason",
        "total_record",
        "ordering",
        "matched_start_row",
        "name_filter_ignored_for",
        "matched_query",
    ):
        if key in src and src[key] is not None:
            out[key] = src[key]
    if cache_hit is not None:
        out["cache_hit"] = bool(cache_hit)
    elif "cache_hit" in src:
        out["cache_hit"] = src["cache_hit"]
    return out


def _category_matches(product: dict[str, Any], primary_category_id: str) -> bool:
    raw = product.get("primary_category_id")
    if raw is None or str(raw).strip() == "":
        return False
    return str(raw).strip() == str(primary_category_id).strip()


def _top_brand_counts(products: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for product in products:
        brand = str(product.get("brand") or "").strip()
        counts[brand if brand else "(empty)"] += 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    return [{"brand": brand, "count": count} for brand, count in ranked[:limit]]


def _no_brand_item_samples(
    products: list[dict[str, Any]], *, limit: int = 5
) -> list[str]:
    samples: list[str] = []
    for product in products:
        if not is_no_brand_label(product.get("brand")):
            continue
        item_id = str(product.get("daraz_item_id") or "").strip()
        if not item_id:
            continue
        samples.append(item_id)
        if len(samples) >= limit:
            break
    return samples


def _list_all_workspace_products(repo: Any, workspace_id: str) -> list[dict[str, Any]]:
    """Paginate local synced products only — no Daraz HTTP."""
    items: list[dict[str, Any]] = []
    page = 1
    while page <= 500:
        batch = repo.list_daraz_products(
            workspace_id,
            {"page": page, "page_size": 200, "sort": "created_at_desc"},
        )
        rows = list(batch.get("items") or [])
        items.extend(rows)
        total = int(batch.get("total") or 0)
        if not rows or len(items) >= total:
            break
        page += 1
    return items


def build_catalog_brand_evidence(
    workspace_id: str,
    *,
    store_uuid: str | None,
    primary_category_id: str,
    repo: Any | None = None,
) -> dict[str, Any]:
    """Local synced-catalog evidence for brand values (read-only DB/memory).

    Does not call Daraz. Does not change create/resolve status.
    """
    repo = repo or get_repo()
    products = _list_all_workspace_products(repo, workspace_id)
    if not products:
        return {
            "status": "NOT_SYNCED",
            "message": "No synced products in this workspace — sync products first",
            "primary_category_id": str(primary_category_id),
            "workspace": {
                "product_count": 0,
                "category_product_count": 0,
                "top_brands_overall": [],
                "top_brands_in_category": [],
                "no_brand_sample_item_ids": [],
            },
            "destination_store": {
                "store_id": None,
                "product_count": 0,
                "category_product_count": 0,
                "top_brands_overall": [],
                "top_brands_in_category": [],
                "no_brand_sample_item_ids": [],
            },
        }

    cat = str(primary_category_id).strip()
    store_products = [
        p
        for p in products
        if store_uuid and str(p.get("store_id") or "") == str(store_uuid)
    ]
    workspace_in_cat = [p for p in products if _category_matches(p, cat)]
    store_in_cat = [p for p in store_products if _category_matches(p, cat)]

    dest_store_id = None
    if store_uuid:
        store = repo.get_store_by_uuid(workspace_id, store_uuid)
        if store:
            dest_store_id = store.get("store_id")

    return {
        "status": "OK",
        "message": (
            "Local synced catalog brand frequencies for this category "
            "(Seller Center may offer 'Set as No Brand' without a brands/query row)"
        ),
        "primary_category_id": cat,
        "workspace": {
            "product_count": len(products),
            "category_product_count": len(workspace_in_cat),
            "top_brands_overall": _top_brand_counts(products),
            "top_brands_in_category": _top_brand_counts(workspace_in_cat),
            "no_brand_sample_item_ids": _no_brand_item_samples(workspace_in_cat),
        },
        "destination_store": {
            "store_id": dest_store_id,
            "product_count": len(store_products),
            "category_product_count": len(store_in_cat),
            "top_brands_overall": _top_brand_counts(store_products),
            "top_brands_in_category": _top_brand_counts(store_in_cat),
            "no_brand_sample_item_ids": _no_brand_item_samples(store_in_cat),
        },
    }


def diagnose_category_no_brand(
    workspace_id: str,
    *,
    store_ref: str,
    primary_category_id: int | str,
    clear_cache: bool = False,
) -> dict[str, Any]:
    """Inspect category brands for a verified No Brand option (read-only).

    Calls only ``/category/brands/query`` via the store client for API lookup.
    Catalog evidence is local synced data only. Does not call CreateProduct,
    UpdatePrice, or image migration. Never invents brand IDs. Create stays
    blocked unless status is NO_BRAND from the brands API row.
    """
    if clear_cache:
        clear_no_brand_cache_for_tests()

    cat = str(primary_category_id).strip() if primary_category_id is not None else ""
    if not cat:
        return {
            "status": "UNVERIFIED",
            "reason": "primary_category_id_required",
            "no_brand_found": False,
            "brand": None,
            "brand_id": None,
        }

    repo = get_repo()
    store = repo.get_store(workspace_id, store_ref) or repo.get_store_by_uuid(
        workspace_id, store_ref
    )
    if not store:
        return {
            "status": "UNVERIFIED",
            "reason": "store_not_found",
            "no_brand_found": False,
            "brand": None,
            "brand_id": None,
            "primary_category_id": cat,
        }

    catalog_evidence = build_catalog_brand_evidence(
        workspace_id,
        store_uuid=str(store.get("id") or ""),
        primary_category_id=cat,
        repo=repo,
    )

    try:
        from src.ops import client_for_store
        from src.token_refresh import refresh_store_tokens

        try:
            refresh_store_tokens(
                store_ids=[str(store.get("store_id") or store.get("id"))],
                within_minutes=30,
                workspace_id=workspace_id,
            )
            store = (
                repo.get_store(workspace_id, store_ref)
                or repo.get_store_by_uuid(workspace_id, store_ref)
                or store
            )
        except Exception:  # noqa: BLE001
            pass
        client = client_for_store(store)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "UNVERIFIED",
            "reason": f"token_unavailable:{type(exc).__name__}",
            "no_brand_found": False,
            "brand": None,
            "brand_id": None,
            "primary_category_id": cat,
            "store_id": store.get("store_id"),
            "catalog_evidence": catalog_evidence,
        }

    calls: list[dict[str, Any]] = []

    def query_brands(**kwargs: Any) -> dict[str, Any]:
        # Record safe params only (no tokens).
        calls.append(
            {
                "primary_category_id": kwargs.get("primary_category_id"),
                "start_row": kwargs.get("start_row"),
                "page_size": kwargs.get("page_size"),
                "name": kwargs.get("name"),
            }
        )
        return client.query_category_brands(**kwargs)

    try:
        resolution = resolve_destination_no_brand(
            primary_category_id=cat,
            query_brands=query_brands,
            marketplace=str(store.get("country") or "pk"),
        )
    except DarazApiError as exc:
        return {
            "status": "UNVERIFIED",
            "reason": f"daraz_api:{exc.code or type(exc).__name__}",
            "no_brand_found": False,
            "brand": None,
            "brand_id": None,
            "primary_category_id": cat,
            "store_id": store.get("store_id"),
            "lookup_calls": len(calls),
            "catalog_evidence": catalog_evidence,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "UNVERIFIED",
            "reason": f"lookup_error:{type(exc).__name__}",
            "no_brand_found": False,
            "brand": None,
            "brand_id": None,
            "primary_category_id": cat,
            "store_id": store.get("store_id"),
            "lookup_calls": len(calls),
            "catalog_evidence": catalog_evidence,
        }

    lookup = public_lookup_fields(
        resolution.get("lookup") or {},
        cache_hit=bool(resolution.get("cache_hit")),
    )
    brand = resolution.get("brand")
    brand_id = resolution.get("brand_id")
    found = (
        resolution.get("status") == "NO_BRAND"
        and is_no_brand_label(brand)
        and bool(brand)
    )

    base = {
        "primary_category_id": cat,
        "store_id": store.get("store_id"),
        "lookup": lookup,
        "lookup_calls": len(calls),
        "timings_ms": resolution.get("timings_ms"),
        "catalog_evidence": catalog_evidence,
    }

    if found:
        return {
            **base,
            "status": "NO_BRAND",
            "reason": "confirmed_no_brand_row",
            "no_brand_found": True,
            "brand": brand,
            "brand_id": brand_id,  # only if Daraz returned it — never invented
            "used_no_brand": True,
        }

    # Full catalog scan completed without a No Brand row → unavailable.
    # Status logic unchanged: complete → UNRESOLVED; otherwise UNVERIFIED.
    # Catalog evidence is informational only — never unlocks create.
    if lookup.get("complete") is True:
        return {
            **base,
            "status": "UNRESOLVED",
            "reason": str(resolution.get("reason") or "no_brand_unavailable"),
            "no_brand_found": False,
            "brand": None,
            "brand_id": None,
        }

    return {
        **base,
        "status": "UNVERIFIED",
        "reason": str(
            lookup.get("stop_reason")
            or resolution.get("reason")
            or "no_brand_scan_incomplete"
        ),
        "no_brand_found": False,
        "brand": None,
        "brand_id": None,
    }
