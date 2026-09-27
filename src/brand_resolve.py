"""Resolve destination brand — ALWAYS No Brand for CreateProduct.

Source brand may be stored as provenance only; never used as destination brand.
"""

from __future__ import annotations

import time
from typing import Any, Callable


BrandLookup = Callable[..., dict[str, Any]]

# marketplace/category_id → (ts, resolution dict)
_NO_BRAND_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_NO_BRAND_TTL_S = 60 * 60


def clear_no_brand_cache_for_tests() -> None:
    _NO_BRAND_CACHE.clear()


def _normalize(name: str | None) -> str:
    return " ".join((name or "").strip().lower().split())


def _module_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return []
    module = (
        data.get("module")
        or data.get("brands")
        or data.get("brand")
        or []
    )
    if isinstance(module, dict):
        module = [module]
    return [m for m in module if isinstance(m, dict)]


def _find_no_brand_row(payload: dict[str, Any]) -> dict[str, Any] | None:
    for row in _module_rows(payload):
        names = [
            row.get("name"),
            row.get("name_en"),
            row.get("global_identifier"),
        ]
        if any(_normalize(str(n)) == "no brand" for n in names if n):
            return row
    return None


def resolve_destination_no_brand(
    *,
    primary_category_id: int | str | None,
    query_brands: BrandLookup,
    marketplace: str | None = None,
    page_size: int = 50,
) -> dict[str, Any]:
    """Resolve Daraz's valid No Brand for marketplace + category (cached)."""
    mp = (marketplace or "pk").strip().lower() or "pk"
    cache_key = f"{mp}/{primary_category_id}"
    now = time.time()
    if cache_key in _NO_BRAND_CACHE:
        ts, cached = _NO_BRAND_CACHE[cache_key]
        if now - ts < _NO_BRAND_TTL_S:
            out = dict(cached)
            out["cache_hit"] = True
            return out

    t0 = time.perf_counter()
    try:
        payload = query_brands(
            primary_category_id=primary_category_id,
            start_row=0,
            page_size=page_size,
            name="No Brand",
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "UNRESOLVED",
            "brand": None,
            "brand_id": None,
            "message": f"Unable to query brands for No Brand: {exc}",
            "used_no_brand": False,
            "reason": "no_brand_unavailable",
            "timings_ms": {"brand_resolution_ms": round((time.perf_counter() - t0) * 1000, 1)},
        }

    row = _find_no_brand_row(payload)
    if row:
        result = {
            "status": "NO_BRAND",
            "brand": row.get("name") or "No Brand",
            "brand_id": row.get("brand_id"),
            "message": "Using destination No Brand",
            "used_no_brand": True,
            "cache_hit": False,
            "timings_ms": {
                "brand_resolution_ms": round((time.perf_counter() - t0) * 1000, 1)
            },
        }
        _NO_BRAND_CACHE[cache_key] = (now, {k: v for k, v in result.items() if k != "timings_ms"})
        return result

    # Category brand query returned rows but no No Brand — do not invent.
    # Some categories still accept the literal string; only use it when the
    # module is empty (query returned nothing useful).
    rows = _module_rows(payload)
    if not rows:
        result = {
            "status": "NO_BRAND",
            "brand": "No Brand",
            "brand_id": None,
            "message": "Using literal No Brand (exact module row not confirmed)",
            "used_no_brand": True,
            "cache_hit": False,
            "timings_ms": {
                "brand_resolution_ms": round((time.perf_counter() - t0) * 1000, 1)
            },
        }
        _NO_BRAND_CACHE[cache_key] = (now, {k: v for k, v in result.items() if k != "timings_ms"})
        return result

    return {
        "status": "UNRESOLVED",
        "brand": None,
        "brand_id": None,
        "message": "Category has no valid No Brand option",
        "used_no_brand": False,
        "reason": "no_brand_unavailable",
        "timings_ms": {"brand_resolution_ms": round((time.perf_counter() - t0) * 1000, 1)},
        "candidates_sample": [
            {"name": r.get("name"), "brand_id": r.get("brand_id")} for r in rows[:5]
        ],
    }


def resolve_brand_for_category(
    *,
    source_brand: str | None,
    primary_category_id: int | str | None,
    query_brands: BrandLookup,
    page_size: int = 50,
    marketplace: str | None = None,
) -> dict[str, Any]:
    """ALWAYS resolve destination No Brand. Source brand is provenance only.

    Phase 4D.5.2B brand policy: never preserve or match source brand for
    CreateProduct.
    """
    result = resolve_destination_no_brand(
        primary_category_id=primary_category_id,
        query_brands=query_brands,
        marketplace=marketplace,
        page_size=page_size,
    )
    raw = (source_brand or "").strip()
    if raw:
        result["source_brand"] = raw
        result["source_brand_ignored"] = True
        if result.get("status") == "NO_BRAND":
            result["message"] = (
                f"Source brand '{raw}' ignored — destination always No Brand"
            )
    else:
        result["source_brand_ignored"] = True
    return result
