"""Resolve source brand string into a destination-category-valid brand."""

from __future__ import annotations

from typing import Any, Callable


BrandLookup = Callable[..., dict[str, Any]]


def _normalize(name: str | None) -> str:
    return " ".join((name or "").strip().lower().split())


def _module_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return []
    module = data.get("module") or data.get("brands") or []
    return [m for m in module if isinstance(m, dict)]


def resolve_brand_for_category(
    *,
    source_brand: str | None,
    primary_category_id: int | str | None,
    query_brands: BrandLookup,
    page_size: int = 50,
) -> dict[str, Any]:
    """Exact-match brand resolution against /category/brands/query.

    Outcomes:
      EXACT_MATCH — use destination brand name (and brand_id if present)
      NO_BRAND — source empty/No Brand and destination has No Brand
      UNRESOLVED — require operator correction (never invent brand_id)
    """
    raw = (source_brand or "").strip()
    norm = _normalize(raw)

    if not norm or norm in {"no brand", "nobrand", "n/a", "na", "-"}:
        # Try to confirm "No Brand" exists for the category
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
            }
        for row in _module_rows(payload):
            names = [
                row.get("name"),
                row.get("name_en"),
                row.get("global_identifier"),
            ]
            if any(_normalize(str(n)) == "no brand" for n in names if n):
                return {
                    "status": "NO_BRAND",
                    "brand": row.get("name") or "No Brand",
                    "brand_id": row.get("brand_id"),
                    "message": "Using destination No Brand",
                }
        # Still acceptable representation many categories use as string
        return {
            "status": "NO_BRAND",
            "brand": "No Brand",
            "brand_id": None,
            "message": "Using literal No Brand (exact module row not confirmed)",
        }

    # Exact search by name
    try:
        payload = query_brands(
            primary_category_id=primary_category_id,
            start_row=0,
            page_size=page_size,
            name=raw,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "UNRESOLVED",
            "brand": None,
            "brand_id": None,
            "message": f"Brand query failed: {exc}",
        }

    rows = _module_rows(payload)
    exact: dict[str, Any] | None = None
    for row in rows:
        candidates = [
            row.get("name"),
            row.get("name_en"),
            row.get("global_identifier"),
        ]
        if any(_normalize(str(c)) == norm for c in candidates if c is not None):
            exact = row
            break

    if exact:
        return {
            "status": "EXACT_MATCH",
            "brand": exact.get("name") or exact.get("name_en") or raw,
            "brand_id": exact.get("brand_id"),
            "message": "Exact brand match on destination category",
        }

    return {
        "status": "UNRESOLVED",
        "brand": None,
        "brand_id": None,
        "source_brand": raw,
        "message": (
            f"Brand '{raw}' not found as an exact match for category "
            f"{primary_category_id}; operator correction required"
        ),
        "candidates_sample": [
            {
                "name": r.get("name"),
                "brand_id": r.get("brand_id"),
            }
            for r in rows[:5]
        ],
    }
