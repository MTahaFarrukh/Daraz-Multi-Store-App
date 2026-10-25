"""Resolve destination brand — ALWAYS No Brand for CreateProduct.

Source brand may be stored as provenance only; never used as destination brand.

Evidence (data/phase4a_spike_brands_migrate.json):
- ``/category/brands/query`` returns ``data.module[]`` with
  ``name`` / ``name_en`` / ``global_identifier`` / ``brand_id``.
- ``total_record`` can exceed 70k brands.
- The ``name`` query parameter is often **ignored** on Daraz PK (filtered
  request still returns the unfiltered first page). A shallow page scan then
  falsely reports ``no_brand_unavailable`` even when Seller Center shows
  selectable ``No Brand``.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


BrandLookup = Callable[..., dict[str, Any]]

# marketplace/category_id → (ts, resolution dict)
_NO_BRAND_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_NO_BRAND_TTL_S = 60 * 60
_NO_BRAND_LOCK = threading.Lock()

# Keep create latency bounded while covering large brand catalogs.
_MAX_LOOKUP_CALLS = 48
_SCAN_PAGE_SIZE = 200  # Lazada/Daraz docs allow up to 200
_NAME_FILTER_VARIANTS = ("No Brand", "No brand", "NO BRAND", "no brand")


def clear_no_brand_cache_for_tests() -> None:
    with _NO_BRAND_LOCK:
        _NO_BRAND_CACHE.clear()


def _normalize(name: str | None) -> str:
    return " ".join(
        (name or "").replace("_", " ").replace("-", " ").strip().lower().split()
    )


def _compact(name: str | None) -> str:
    """Lowercase alphanumerics only — catches NoBrand / NO_BRAND / no-brand."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def is_no_brand_label(value: Any) -> bool:
    """True when a Daraz brand field denotes the official No Brand option."""
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    if _normalize(text) == "no brand":
        return True
    return _compact(text) == "nobrand"


def _row_labels(row: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in (
        "name",
        "name_en",
        "global_identifier",
        "brandName",
        "brand_name",
        "Brand",
    ):
        val = row.get(key)
        if val is not None and str(val).strip():
            labels.append(str(val))
    return labels


def _row_primary_name(row: dict[str, Any]) -> str | None:
    """Prefer the marketplace display name that CreateProduct expects."""
    for key in ("name", "name_en", "brandName", "brand_name"):
        val = row.get(key)
        if is_no_brand_label(val):
            return str(val)
    for key in ("global_identifier",):
        val = row.get(key)
        if is_no_brand_label(val):
            # Prefer a spaced display form for XML when only identifier matched.
            return "No Brand" if _compact(str(val)) == "nobrand" else str(val)
    return None


def _row_brand_id(row: dict[str, Any]) -> Any:
    for key in ("brand_id", "brandId", "id"):
        if row.get(key) is not None and str(row.get(key)).strip() != "":
            return row.get(key)
    return None


def _module_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []
    module = (
        data.get("module")
        or data.get("brands")
        or data.get("brand")
        or data.get("list")
        or []
    )
    if isinstance(module, dict):
        module = [module]
    return [row for row in module if isinstance(row, dict)] if isinstance(module, list) else []


def _total_record(payload: dict[str, Any]) -> int | None:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    raw = data.get("total_record")
    if raw is None:
        raw = data.get("totalRecord")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def find_no_brand_row(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Public helper — locate No Brand in a brands/query payload."""
    for row in _module_rows(payload):
        if any(is_no_brand_label(label) for label in _row_labels(row)):
            return row
    return None


# Back-compat alias used by older tests/imports.
_find_no_brand_row = find_no_brand_row


def _name_filter_looks_ineffective(payload: dict[str, Any], query: str) -> bool:
    """Detect Daraz ignoring ``name`` (returns unfiltered first page)."""
    rows = _module_rows(payload)
    if not rows:
        return False
    if find_no_brand_row(payload):
        return False
    q = _normalize(query)
    q_compact = _compact(query)
    # Real filtered hits usually share a prefix / contain the query token.
    for row in rows[:8]:
        for label in _row_labels(row):
            norm = _normalize(label)
            compact = _compact(label)
            if (
                norm.startswith(q[: max(2, min(4, len(q)))])
                or q in norm
                or (q_compact and q_compact[:4] in compact)
            ):
                return False
    return True


def _compare_label(label: str | None, target: str = "no brand") -> int:
    left = _normalize(label)
    right = _normalize(target)
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def _query(
    query_brands: BrandLookup,
    *,
    primary_category_id: int | str | None,
    start_row: int,
    page_size: int,
    name: str | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "primary_category_id": primary_category_id,
        "start_row": int(start_row),
        "page_size": int(page_size),
    }
    if name:
        kwargs["name"] = name
    return query_brands(**kwargs)


def resolve_destination_no_brand(
    *,
    primary_category_id: int | str | None,
    query_brands: BrandLookup,
    marketplace: str | None = None,
    page_size: int = _SCAN_PAGE_SIZE,
) -> dict[str, Any]:
    """Resolve Daraz's valid No Brand for marketplace + category (cached)."""
    mp = (marketplace or "pk").strip().lower() or "pk"
    cache_key = f"{mp}/{primary_category_id}"
    now = time.time()
    with _NO_BRAND_LOCK:
        if cache_key in _NO_BRAND_CACHE:
            ts, cached = _NO_BRAND_CACHE[cache_key]
            if now - ts < _NO_BRAND_TTL_S:
                out = dict(cached)
                out["cache_hit"] = True
                return out

    t0 = time.perf_counter()
    page_size = max(20, min(int(page_size or _SCAN_PAGE_SIZE), 200))
    diagnostics: dict[str, Any] = {
        "pages": 0,
        "complete": False,
        "name_filter_effective": None,
        "strategy": None,
    }
    row: dict[str, Any] | None = None

    try:
        # 1) Name-filter fast path (often ignored on Daraz — still try variants).
        filter_ineffective = False
        for variant in _NAME_FILTER_VARIANTS:
            payload = _query(
                query_brands,
                primary_category_id=primary_category_id,
                start_row=0,
                page_size=page_size,
                name=variant,
            )
            diagnostics["pages"] += 1
            hit = find_no_brand_row(payload)
            if hit:
                row = hit
                diagnostics["strategy"] = "name_filter"
                diagnostics["name_filter_effective"] = True
                diagnostics["matched_query"] = variant
                break
            if _name_filter_looks_ineffective(payload, variant):
                filter_ineffective = True
                diagnostics["name_filter_effective"] = False
                diagnostics["name_filter_ignored_for"] = variant
                # Don't burn remaining variants if the API clearly ignores name.
                break
        else:
            if diagnostics.get("name_filter_effective") is None:
                diagnostics["name_filter_effective"] = False

        # 2) Catalog scan — binary search by display name, then local window.
        if row is None:
            diagnostics["strategy"] = (
                "binary_scan_after_ineffective_filter"
                if filter_ineffective
                else "binary_scan"
            )
            probe = _query(
                query_brands,
                primary_category_id=primary_category_id,
                start_row=0,
                page_size=page_size,
            )
            diagnostics["pages"] += 1
            row = find_no_brand_row(probe)
            total = _total_record(probe)
            diagnostics["total_record"] = total

            if row is None and total and total > 0:
                lo = 0
                hi = max(0, total - 1)
                visited: set[int] = set()
                while lo <= hi and diagnostics["pages"] < _MAX_LOOKUP_CALLS:
                    mid = (lo + hi) // 2
                    start = (mid // page_size) * page_size
                    if start in visited:
                        # Degenerate ordering — step forward linearly.
                        start = lo if lo not in visited else lo + page_size
                    if start < 0 or (total is not None and start >= total):
                        break
                    visited.add(start)
                    payload = _query(
                        query_brands,
                        primary_category_id=primary_category_id,
                        start_row=start,
                        page_size=page_size,
                    )
                    diagnostics["pages"] += 1
                    rows = _module_rows(payload)
                    hit = find_no_brand_row(payload)
                    if hit:
                        row = hit
                        diagnostics["matched_start_row"] = start
                        break
                    if not rows:
                        diagnostics["stop_reason"] = "empty_page"
                        break
                    first = _row_labels(rows[0])[0] if _row_labels(rows[0]) else ""
                    last = _row_labels(rows[-1])[0] if _row_labels(rows[-1]) else first
                    # If this window should contain "No Brand" alphabetically but
                    # didn't, scan immediate neighbors before giving up.
                    if (
                        _compare_label(first) <= 0 <= _compare_label(last)
                        or _normalize(first)
                        <= "no brand"
                        <= _normalize(last)
                    ):
                        for neighbor in (
                            max(0, start - page_size),
                            start + page_size,
                        ):
                            if neighbor in visited:
                                continue
                            if total is not None and neighbor >= total:
                                continue
                            visited.add(neighbor)
                            near = _query(
                                query_brands,
                                primary_category_id=primary_category_id,
                                start_row=neighbor,
                                page_size=page_size,
                            )
                            diagnostics["pages"] += 1
                            hit = find_no_brand_row(near)
                            if hit:
                                row = hit
                                diagnostics["matched_start_row"] = neighbor
                                break
                        if row:
                            break
                        # Window claimed alphabetical containment without a hit —
                        # ordering is unreliable; widen with linear sampling.
                        diagnostics["ordering"] = "unreliable_in_window"
                        lo = min(lo + page_size, hi + 1)
                        continue
                    if _compare_label(last) < 0:
                        lo = start + page_size
                    elif _compare_label(first) > 0:
                        hi = start - 1
                    else:
                        lo = start + page_size

            if row is None and diagnostics["pages"] < _MAX_LOOKUP_CALLS:
                # Final linear pass from 0 with large pages until budget/total.
                start = 0
                seen_fingerprints: set[tuple] = set()
                while diagnostics["pages"] < _MAX_LOOKUP_CALLS:
                    payload = _query(
                        query_brands,
                        primary_category_id=primary_category_id,
                        start_row=start,
                        page_size=page_size,
                    )
                    diagnostics["pages"] += 1
                    rows = _module_rows(payload)
                    hit = find_no_brand_row(payload)
                    if hit:
                        row = hit
                        diagnostics["strategy"] = "linear_scan"
                        diagnostics["matched_start_row"] = start
                        break
                    fingerprint = tuple(
                        (str(_row_brand_id(r) or ""), str((_row_labels(r) or [""])[0]))
                        for r in rows
                    )
                    if rows and fingerprint in seen_fingerprints:
                        diagnostics["stop_reason"] = "repeated_page"
                        break
                    seen_fingerprints.add(fingerprint)
                    total = total if total is not None else _total_record(payload)
                    if total is not None and (start + len(rows)) >= total:
                        diagnostics["complete"] = True
                        diagnostics["stop_reason"] = "reported_total"
                        break
                    if not rows or len(rows) < page_size:
                        diagnostics["stop_reason"] = "empty_or_short_page"
                        break
                    start += page_size
                else:
                    diagnostics["stop_reason"] = "page_limit"

    except Exception as exc:  # lookup failure is unresolved, never literal fallback
        diagnostics["stop_reason"] = "lookup_error"
        diagnostics["error_type"] = type(exc).__name__

    if row:
        name = _row_primary_name(row)
        brand_id = _row_brand_id(row)
        if name:
            result = {
                "status": "NO_BRAND",
                "brand": name,
                "brand_id": brand_id,
                "message": "Confirmed official No Brand row",
                "used_no_brand": True,
                "cache_hit": False,
                "lookup": diagnostics,
                "timings_ms": {
                    "brand_resolution_ms": round((time.perf_counter() - t0) * 1000, 1)
                },
            }
            with _NO_BRAND_LOCK:
                _NO_BRAND_CACHE[cache_key] = (
                    now,
                    {k: v for k, v in result.items() if k != "timings_ms"},
                )
            return result

    return {
        "status": "UNRESOLVED",
        "brand": None,
        "brand_id": None,
        "message": "No Brand could not be confirmed for this category",
        "used_no_brand": False,
        "reason": "no_brand_unavailable",
        "lookup": diagnostics,
        "timings_ms": {
            "brand_resolution_ms": round((time.perf_counter() - t0) * 1000, 1)
        },
    }


def resolve_brand_for_category(
    *,
    source_brand: str | None,
    primary_category_id: int | str | None,
    query_brands: BrandLookup,
    page_size: int = _SCAN_PAGE_SIZE,
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
