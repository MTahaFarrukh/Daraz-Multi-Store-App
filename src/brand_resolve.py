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

# Diagnostic-only deep scan (never used by create-path resolve).
_DEEP_SCAN_MAX_PAGES = 60
_DEEP_SCAN_DELAY_S = 0.05


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


def is_rate_limited_error(exc: BaseException) -> bool:
    """True when Daraz/Lazada signals throttle — never blind-retry."""
    code = str(getattr(exc, "code", "") or "")
    msg = str(exc).lower()
    http = getattr(exc, "http_status", None)
    if http == 429:
        return True
    code_l = code.lower()
    if "apicalllimit" in code_l or "call.limit" in code_l or "ratelimit" in code_l:
        return True
    if any(
        tok in msg
        for tok in (
            "rate limit",
            "too many requests",
            "api call limit",
            "qps",
            "throttl",
        )
    ):
        return True
    return False


def _evidence_uniqueness_verified(evidence: dict[str, Any] | None) -> bool:
    ev = evidence or {}
    if ev.get("uniqueness_verified") is True:
        return True
    try:
        candidates_total = int(ev.get("candidates_total") or 0)
    except (TypeError, ValueError):
        candidates_total = 0
    return bool(ev.get("complete") is True and candidates_total == 1)


def _persisted_no_brand_resolution(
    *,
    marketplace: str,
    primary_category_id: int | str | None,
) -> dict[str, Any] | None:
    """Read verified NO_BRAND from local persistence (no Daraz call).

    Create may only proceed when ``uniqueness_verified`` is true in evidence
    (complete catalog scan with exactly one distinct No Brand brand_id).
    """
    if primary_category_id is None or str(primary_category_id).strip() == "":
        return None
    try:
        from src.db.repo import get_repo

        row = get_repo().get_verified_category_no_brand(
            marketplace, str(primary_category_id).strip()
        )
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    brand = row.get("brand_name") or row.get("brand")
    brand_id = row.get("brand_id")
    if not is_no_brand_label(brand) or brand_id in (None, ""):
        return None
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    uniqueness_ok = _evidence_uniqueness_verified(evidence)
    return {
        "status": "NO_BRAND",
        "brand": brand,
        "brand_id": brand_id,
        "message": (
            "Uniqueness-verified No Brand from persisted marketplace/category cache"
            if uniqueness_ok
            else "Persisted No Brand row — uniqueness not verified (create blocked)"
        ),
        "used_no_brand": True,
        "cache_hit": False,
        "persisted": True,
        "uniqueness_verified": uniqueness_ok,
        "lookup": {
            "strategy": "persisted_verified",
            "pages": 0,
            "complete": bool(evidence.get("complete")) if evidence else uniqueness_ok,
            "matched_start_row": evidence.get("matched_start_row"),
            "total_record": evidence.get("total_record"),
            "candidates_total": evidence.get("candidates_total"),
            "uniqueness_verified": uniqueness_ok,
            "verified_at": row.get("verified_at"),
        },
    }


def deep_scan_no_brand(
    *,
    primary_category_id: int | str | None,
    query_brands: BrandLookup,
    marketplace: str | None = None,
    page_size: int = _SCAN_PAGE_SIZE,
    scan_from_start_row: int = 0,
    max_pages: int = _DEEP_SCAN_MAX_PAGES,
    delay_s: float = _DEEP_SCAN_DELAY_S,
    prior_candidates: list[dict[str, Any]] | None = None,
    verify_uniqueness: bool = False,
) -> dict[str, Any]:
    """Resumable linear brand catalog scan for diagnostics only.

    Does not change create-path ``_MAX_LOOKUP_CALLS`` budget. Ordering is not
    trusted; ``name`` filter is not used. Stops on rate limit without retry.

    When ``verify_uniqueness=True``, keep scanning past the first match until
    the catalog end (or budget/rate-limit). Uniqueness is proven only when
    ``complete=True`` and exactly one distinct No Brand ``brand_id`` exists.
    """
    t0 = time.perf_counter()
    mp = (marketplace or "pk").strip().lower() or "pk"
    page_size = max(20, min(int(page_size or _SCAN_PAGE_SIZE), 200))
    max_pages = max(1, min(int(max_pages or _DEEP_SCAN_MAX_PAGES), 60))
    start = max(0, int(scan_from_start_row or 0))
    delay_s = max(0.0, float(delay_s or 0.0))
    verify_uniqueness = bool(verify_uniqueness)

    # Distinct brand_id → candidate
    candidates: dict[str, dict[str, Any]] = {}
    for c in prior_candidates or []:
        if not isinstance(c, dict):
            continue
        bid = c.get("brand_id")
        if bid is None or str(bid).strip() == "":
            continue
        candidates[str(bid)] = {
            "brand": c.get("brand") or c.get("name"),
            "brand_id": bid,
            "matched_start_row": c.get("matched_start_row"),
        }

    diagnostics: dict[str, Any] = {
        "strategy": (
            "deep_linear_scan_verify_uniqueness"
            if verify_uniqueness
            else "deep_linear_scan"
        ),
        "pages": 0,
        "complete": False,
        "name_filter_effective": False,
        "scan_from_start_row": start,
        "scanned_rows": 0,
        "next_start_row": start,
        "total_record": None,
        "marketplace": mp,
        "verify_uniqueness": verify_uniqueness,
        "uniqueness_verified": False,
        "candidates_total": len(candidates),
    }

    try:
        while diagnostics["pages"] < max_pages:
            if diagnostics["pages"] > 0 and delay_s > 0:
                time.sleep(delay_s)
            try:
                payload = _query(
                    query_brands,
                    primary_category_id=primary_category_id,
                    start_row=start,
                    page_size=page_size,
                )
            except Exception as exc:  # noqa: BLE001
                if is_rate_limited_error(exc):
                    diagnostics["stop_reason"] = "rate_limited"
                    diagnostics["error_type"] = type(exc).__name__
                    break
                diagnostics["stop_reason"] = "lookup_error"
                diagnostics["error_type"] = type(exc).__name__
                break

            diagnostics["pages"] += 1
            rows = _module_rows(payload)
            total = _total_record(payload)
            if total is not None:
                diagnostics["total_record"] = total

            for row in rows:
                if not any(is_no_brand_label(label) for label in _row_labels(row)):
                    continue
                name = _row_primary_name(row) or "No Brand"
                brand_id = _row_brand_id(row)
                if brand_id is None or str(brand_id).strip() == "":
                    continue
                key = str(brand_id)
                if key not in candidates:
                    candidates[key] = {
                        "brand": name,
                        "brand_id": brand_id,
                        "matched_start_row": start,
                    }

            diagnostics["scanned_rows"] = int(diagnostics["scanned_rows"]) + len(rows)
            next_row = start + (len(rows) if rows else page_size)

            if total is not None and next_row >= total:
                diagnostics["complete"] = True
                diagnostics["stop_reason"] = "reported_total"
                diagnostics["next_start_row"] = total
                break
            if not rows or len(rows) < page_size:
                diagnostics["complete"] = True
                diagnostics["stop_reason"] = "empty_or_short_page"
                diagnostics["next_start_row"] = next_row
                break

            start = next_row
            diagnostics["next_start_row"] = start
        else:
            if not diagnostics.get("stop_reason"):
                diagnostics["stop_reason"] = "page_budget"
                diagnostics["next_start_row"] = start

    except Exception as exc:  # noqa: BLE001
        if is_rate_limited_error(exc):
            diagnostics["stop_reason"] = "rate_limited"
        else:
            diagnostics["stop_reason"] = "lookup_error"
        diagnostics["error_type"] = type(exc).__name__

    cand_list = list(candidates.values())
    diagnostics["candidates_total"] = len(cand_list)
    uniqueness_ok = bool(diagnostics.get("complete") and len(cand_list) == 1)
    diagnostics["uniqueness_verified"] = uniqueness_ok
    timings = {
        "brand_resolution_ms": round((time.perf_counter() - t0) * 1000, 1)
    }

    if len(cand_list) > 1:
        diagnostics["stop_reason"] = diagnostics.get("stop_reason") or "ambiguous_no_brand"
        return {
            "status": "UNRESOLVED",
            "brand": None,
            "brand_id": None,
            "message": "Multiple distinct No Brand brand_id values found",
            "used_no_brand": False,
            "reason": "ambiguous_no_brand",
            "uniqueness_verified": False,
            "candidates": cand_list,
            "lookup": diagnostics,
            "timings_ms": timings,
        }

    if len(cand_list) == 1:
        only = cand_list[0]
        diagnostics["matched_start_row"] = only.get("matched_start_row")
        if uniqueness_ok:
            return {
                "status": "NO_BRAND",
                "brand": only.get("brand"),
                "brand_id": only.get("brand_id"),
                "message": (
                    "Confirmed unique No Brand brand_id after complete catalog scan"
                ),
                "used_no_brand": True,
                "cache_hit": False,
                "uniqueness_verified": True,
                "candidates": cand_list,
                "lookup": diagnostics,
                "timings_ms": timings,
            }
        # Found a candidate but catalog not fully scanned — uniqueness unproven.
        if verify_uniqueness:
            return {
                "status": "UNVERIFIED",
                "brand": only.get("brand"),
                "brand_id": only.get("brand_id"),
                "message": (
                    "Uniqueness scan incomplete — continue from next_start_row"
                ),
                "used_no_brand": False,
                "reason": str(
                    diagnostics.get("stop_reason") or "uniqueness_scan_incomplete"
                ),
                "uniqueness_verified": False,
                "candidates": cand_list,
                "lookup": diagnostics,
                "timings_ms": timings,
            }
        return {
            "status": "NO_BRAND",
            "brand": only.get("brand"),
            "brand_id": only.get("brand_id"),
            "message": (
                "No Brand candidate found — uniqueness not verified "
                "(complete catalog scan required before create)"
            ),
            "used_no_brand": True,
            "cache_hit": False,
            "uniqueness_verified": False,
            "candidates": cand_list,
            "lookup": diagnostics,
            "timings_ms": timings,
        }

    if diagnostics.get("complete"):
        return {
            "status": "UNRESOLVED",
            "brand": None,
            "brand_id": None,
            "message": "Complete deep scan found no No Brand row",
            "used_no_brand": False,
            "reason": "not_found_complete_scan",
            "uniqueness_verified": False,
            "candidates": [],
            "lookup": diagnostics,
            "timings_ms": timings,
        }

    return {
        "status": "UNVERIFIED",
        "brand": None,
        "brand_id": None,
        "message": "Deep scan incomplete — No Brand not confirmed",
        "used_no_brand": False,
        "reason": str(diagnostics.get("stop_reason") or "deep_scan_incomplete"),
        "uniqueness_verified": False,
        "candidates": [],
        "lookup": diagnostics,
        "timings_ms": timings,
    }


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

    persisted = _persisted_no_brand_resolution(
        marketplace=mp, primary_category_id=primary_category_id
    )
    if persisted:
        with _NO_BRAND_LOCK:
            _NO_BRAND_CACHE[cache_key] = (
                now,
                {k: v for k, v in persisted.items() if k != "timings_ms"},
            )
        return persisted

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
                total_hit = _total_record(payload)
                if total_hit is not None:
                    diagnostics["total_record"] = total_hit
                    if total_hit == 1:
                        diagnostics["complete"] = True
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
            # Uniqueness is proven only when the create-path scan completed the
            # reported catalog, or the catalog has a single brand row (total=1).
            total = diagnostics.get("total_record")
            try:
                total_i = int(total) if total is not None else None
            except (TypeError, ValueError):
                total_i = None
            uniqueness_ok = bool(
                diagnostics.get("complete") is True or total_i == 1
            )
            result = {
                "status": "NO_BRAND",
                "brand": name,
                "brand_id": brand_id,
                "message": (
                    "Confirmed official No Brand row"
                    if uniqueness_ok
                    else (
                        "Confirmed official No Brand row — uniqueness not verified "
                        "(run Verify uniqueness deep scan before create)"
                    )
                ),
                "used_no_brand": True,
                "cache_hit": False,
                "uniqueness_verified": uniqueness_ok,
                "lookup": {
                    **diagnostics,
                    "uniqueness_verified": uniqueness_ok,
                    "candidates_total": 1,
                },
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
        "uniqueness_verified": False,
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
