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
    return " ".join((name or "").replace("_", " ").replace("-", " ").strip().lower().split())


def _module_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []
    module = data.get("module") or data.get("brands") or data.get("brand") or []
    if isinstance(module, dict):
        module = [module]
    return [row for row in module if isinstance(row, dict)] if isinstance(module, list) else []


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
    diagnostics: dict[str, Any] = {"pages": 0, "complete": False}
    row = None
    try:
        # Filter is a fast path only. A miss cannot establish absence.
        payload = query_brands(primary_category_id=primary_category_id,
                               start_row=0, page_size=page_size, name="No Brand")
        diagnostics["pages"] += 1
        row = _find_no_brand_row(payload)
        seen_pages: set[tuple] = set()
        if row is None:
            for page in range(10):
                payload = query_brands(primary_category_id=primary_category_id,
                                       start_row=page * page_size, page_size=page_size)
                diagnostics["pages"] += 1
                rows = _module_rows(payload)
                row = _find_no_brand_row(payload)
                if row:
                    break
                fingerprint = tuple((str(r.get("brand_id")), str(r.get("name"))) for r in rows)
                if rows and fingerprint in seen_pages:
                    diagnostics["stop_reason"] = "repeated_page"
                    break
                seen_pages.add(fingerprint)
                data = payload.get("data", payload)
                total = data.get("total_record") if isinstance(data, dict) else None
                try:
                    total = int(total) if total is not None else None
                except (ValueError, TypeError):
                    total = None
                if total is not None and (page * page_size + len(rows)) >= total:
                    diagnostics.update(complete=True, stop_reason="reported_total")
                    break
                if not rows or (len(rows) < page_size and total is None):
                    diagnostics["stop_reason"] = "empty_or_short_page_unconfirmed"
                    break
            else:
                diagnostics["stop_reason"] = "page_limit"
    except Exception as exc:  # lookup failure is unresolved, never literal fallback
        diagnostics["stop_reason"] = "lookup_error"
        diagnostics["error_type"] = type(exc).__name__

    if row:
        # Use an actual official name; never manufacture a fallback value.
        name = next((row.get(k) for k in ("name", "name_en")
                     if _normalize(str(row.get(k) or "")) == "no brand"), None)
        if name:
            result = {
                "status": "NO_BRAND", "brand": name, "brand_id": row.get("brand_id"),
                "message": "Confirmed official No Brand row", "used_no_brand": True,
                "cache_hit": False, "lookup": diagnostics,
                "timings_ms": {"brand_resolution_ms": round((time.perf_counter() - t0) * 1000, 1)},
            }
            _NO_BRAND_CACHE[cache_key] = (now, {k: v for k, v in result.items() if k != "timings_ms"})
            return result
    return {
        "status": "UNRESOLVED", "brand": None, "brand_id": None,
        "message": "No Brand could not be confirmed for this category",
        "used_no_brand": False, "reason": "no_brand_unavailable", "lookup": diagnostics,
        "timings_ms": {"brand_resolution_ms": round((time.perf_counter() - t0) * 1000, 1)},
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
