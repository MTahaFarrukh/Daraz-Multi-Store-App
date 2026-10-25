"""Read-only category brand diagnostics — never CreateProduct or image migrate."""

from __future__ import annotations

from typing import Any

from src.brand_resolve import (
    clear_no_brand_cache_for_tests,
    is_no_brand_label,
    resolve_destination_no_brand,
)
from src.daraz_api import DarazApiError
from src.db.repo import get_repo


def diagnose_category_no_brand(
    workspace_id: str,
    *,
    store_ref: str,
    primary_category_id: int | str,
    clear_cache: bool = False,
) -> dict[str, Any]:
    """Inspect category brands for a verified No Brand option (read-only).

    Calls only ``/category/brands/query`` via the store client. Does not call
    CreateProduct, UpdatePrice, or image migration. Never invents brand IDs.
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
        }

    lookup = resolution.get("lookup") or {}
    brand = resolution.get("brand")
    brand_id = resolution.get("brand_id")
    found = (
        resolution.get("status") == "NO_BRAND"
        and is_no_brand_label(brand)
        and bool(brand)
    )

    if found:
        return {
            "status": "NO_BRAND",
            "reason": "confirmed_no_brand_row",
            "no_brand_found": True,
            "brand": brand,
            "brand_id": brand_id,  # only if Daraz returned it — never invented
            "primary_category_id": cat,
            "store_id": store.get("store_id"),
            "used_no_brand": True,
            "lookup": {
                "strategy": lookup.get("strategy"),
                "pages": lookup.get("pages"),
                "name_filter_effective": lookup.get("name_filter_effective"),
                "complete": lookup.get("complete"),
                "stop_reason": lookup.get("stop_reason"),
                "total_record": lookup.get("total_record"),
                "cache_hit": resolution.get("cache_hit"),
            },
            "lookup_calls": len(calls),
            "timings_ms": resolution.get("timings_ms"),
        }

    lookup_meta = {
        "strategy": lookup.get("strategy"),
        "pages": lookup.get("pages"),
        "name_filter_effective": lookup.get("name_filter_effective"),
        "complete": lookup.get("complete"),
        "stop_reason": lookup.get("stop_reason"),
        "total_record": lookup.get("total_record"),
    }

    # Full catalog scan completed without a No Brand row → unavailable.
    if lookup.get("complete") is True:
        return {
            "status": "UNRESOLVED",
            "reason": str(resolution.get("reason") or "no_brand_unavailable"),
            "no_brand_found": False,
            "brand": None,
            "brand_id": None,
            "primary_category_id": cat,
            "store_id": store.get("store_id"),
            "lookup": lookup_meta,
            "lookup_calls": len(calls),
            "timings_ms": resolution.get("timings_ms"),
        }

    # Incomplete / capped / errored scan → do not claim success or absence.
    return {
        "status": "UNVERIFIED",
        "reason": str(
            lookup.get("stop_reason")
            or resolution.get("reason")
            or "no_brand_scan_incomplete"
        ),
        "no_brand_found": False,
        "brand": None,
        "brand_id": None,
        "primary_category_id": cat,
        "store_id": store.get("store_id"),
        "lookup": lookup_meta,
        "lookup_calls": len(calls),
        "timings_ms": resolution.get("timings_ms"),
    }
