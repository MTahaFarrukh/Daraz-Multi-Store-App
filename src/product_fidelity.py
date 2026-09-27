"""Phase 4D.5 — variant pricing, override safety, special-price helpers."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any


def parse_money(value: Any) -> float | None:
    """Parse Rs./numeric money strings into float. Returns None if unusable."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        return n if n == n else None  # NaN guard
    text = str(value).strip()
    if not text:
        return None
    # Drop currency labels before stripping non-digits so "Rs. 1,000" → 1000
    # (not 0.1 from the abbreviation's period).
    text = re.sub(r"(?i)\b(?:rs|pkr|usd|\$)\.?\.?", " ", text)
    text = text.replace(",", "")
    cleaned = re.sub(r"[^\d.]", "", text)
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        n = float(cleaned)
    except ValueError:
        return None
    if n != n:  # NaN
        return None
    return n


def sale_props_label(sale_props: Any) -> str:
    if not isinstance(sale_props, dict) or not sale_props:
        return "Default"
    parts = [f"{k}: {v}" for k, v in sale_props.items() if v is not None and str(v).strip()]
    return " / ".join(parts) if parts else "Default"


def variant_key(variant: dict[str, Any], index: int = 0) -> str:
    for field in (
        "seller_sku",
        "source_daraz_sku_id",
        "daraz_sku_id",
        "source_seller_sku",
    ):
        val = variant.get(field)
        if val is not None and str(val).strip():
            return str(val)
    return f"variant_{index}"


def list_unresolved_price_variants(draft: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, v in enumerate(draft.get("variants") or []):
        if not isinstance(v, dict):
            continue
        if v.get("price") is None:
            out.append(
                {
                    "key": variant_key(v, idx),
                    "label": sale_props_label(v.get("sale_props")),
                    "sale_props": v.get("sale_props") or {},
                    "price": None,
                    "special_price": v.get("special_price"),
                    "price_confidence": v.get("price_confidence") or "missing",
                }
            )
    return out


def variant_regular_prices(draft: dict[str, Any]) -> list[float | None]:
    prices: list[float | None] = []
    for v in draft.get("variants") or []:
        if isinstance(v, dict):
            prices.append(parse_money(v.get("price")))
    return prices


def prices_are_uniform(prices: list[float | None]) -> bool:
    known = [p for p in prices if p is not None]
    if not known:
        return False
    if len(known) != len(prices):
        return False
    return len({round(p, 4) for p in known}) == 1


def apply_price_overrides(
    draft: dict[str, Any],
    *,
    price_override: float | None = None,
    variant_price_overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Apply overrides without flattening differently priced variants.

    Returns a small report describing what was applied / refused.
    """
    variants = [v for v in (draft.get("variants") or []) if isinstance(v, dict)]
    report: dict[str, Any] = {
        "single_override_applied": False,
        "single_override_refused": False,
        "per_variant_applied": 0,
        "reason": None,
    }

    overrides = {
        str(k): float(v)
        for k, v in (variant_price_overrides or {}).items()
        if v is not None and parse_money(v) is not None and float(v) > 0
    }
    for idx, v in enumerate(variants):
        key = variant_key(v, idx)
        if key in overrides:
            v["price"] = overrides[key]
            v["price_confidence"] = "override"
            v["price_source"] = "variant_override"
            report["per_variant_applied"] += 1

    if price_override is None:
        return report
    override = parse_money(price_override)
    if override is None or override <= 0:
        report["reason"] = "invalid_price_override"
        return report

    prices = [parse_money(v.get("price")) for v in variants]
    n = len(variants)
    if n == 0:
        report["reason"] = "no_variants"
        return report

    # Single SKU — always safe.
    if n == 1:
        variants[0]["price"] = override
        variants[0]["price_confidence"] = "override"
        variants[0]["price_source"] = "price_override"
        report["single_override_applied"] = True
        return report

    # All variants already share the same regular price (or none known yet and
    # every slot is still empty — still unsafe for multi; require per-variant).
    if prices_are_uniform(prices):
        for v in variants:
            v["price"] = override
            v["price_confidence"] = "override"
            v["price_source"] = "price_override"
        report["single_override_applied"] = True
        return report

    # Differently priced (or partially known) multi-variant: never flatten.
    report["single_override_refused"] = True
    report["reason"] = "different_variant_prices"
    return report


def validate_variant_pricing(draft: dict[str, Any]) -> list[str]:
    """Return human-readable errors for malformed variant prices."""
    errors: list[str] = []
    variants = draft.get("variants") or []
    if not variants:
        return ["variants:none"]
    for idx, v in enumerate(variants):
        if not isinstance(v, dict):
            errors.append(f"variant[{idx}]:invalid")
            continue
        label = sale_props_label(v.get("sale_props"))
        regular = parse_money(v.get("price"))
        special = parse_money(v.get("special_price"))
        if regular is None:
            errors.append(f"variant[{idx}:{label}]:missing_regular_price")
            continue
        if regular <= 0:
            errors.append(f"variant[{idx}:{label}]:regular_price_not_positive")
        if special is not None:
            if special <= 0:
                errors.append(f"variant[{idx}:{label}]:special_price_not_positive")
            elif special >= regular:
                errors.append(f"variant[{idx}:{label}]:special_price_not_below_regular")
    return errors


def default_special_window() -> tuple[str, str]:
    """Daraz-style special_from/to timestamps spanning ~10 years from now."""
    now = datetime.now(timezone.utc)
    start = now.replace(microsecond=0)
    end = start + timedelta(days=3650)
    fmt = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), end.strftime(fmt)


def classify_duplicate_matches(
    duplicates: list[dict[str, Any]],
    *,
    source_dimensions: list[dict[str, Any]] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Return (ALREADY_EXISTS|POSSIBLE_DUPLICATE|None, best_match).

    When source has meaningful variant dimensions (e.g. Pack of 1 vs Pack of 2),
    demote title-only exact matches that clearly differ on those dimensions.
    """
    if not duplicates:
        return None, None
    best = duplicates[0]
    reason = str(best.get("match_reason") or "")
    score = float(best.get("match_score") or 0)

    # Soft demotion: if source dimensions are present and candidate title encodes
    # a conflicting pack/size token, don't treat as ALREADY_EXISTS.
    if source_dimensions and reason == "title_exact":
        src_vals = {
            str(d.get("source_value") or "").strip().lower()
            for d in source_dimensions
            if d.get("source_value")
        }
        cand_title = str(best.get("title") or best.get("title_en") or "").lower()
        # If source has multiple distinct dimension values across products of same
        # family, exact title match alone is still OK; demote only when candidate
        # title contains a *different* pack/size token than all source values.
        conflicting = False
        for tok in ("pack of 1", "pack of 2", "pack of 3", "small", "medium", "large"):
            if tok in cand_title and src_vals and tok not in src_vals:
                # candidate mentions a variant the source SKUs don't use
                if not any(tok in s for s in src_vals):
                    conflicting = True
                    break
        if conflicting:
            return "POSSIBLE_DUPLICATE", best

    if reason == "title_exact" or score >= 0.95:
        return "ALREADY_EXISTS", best
    if reason in {"title_similar", "category_title_partial"} or score >= 0.6:
        return "POSSIBLE_DUPLICATE", best
    return "POSSIBLE_DUPLICATE", best


def build_update_price_xml(skus: list[dict[str, Any]]) -> str:
    """Build /product/price/update payload for SKU special/regular prices."""
    from xml.sax.saxutils import escape

    def esc(v: Any) -> str:
        return escape("" if v is None else str(v), {"'": "&apos;", '"': "&quot;"})

    nodes: list[str] = []
    for sku in skus:
        seller = sku.get("SellerSku") or sku.get("seller_sku")
        if not seller:
            continue
        parts = [f"<SellerSku>{esc(seller)}</SellerSku>"]
        price = sku.get("Price", sku.get("price"))
        if price is not None:
            parts.append(f"<Price>{esc(price)}</Price>")
        sale = sku.get("SalePrice", sku.get("special_price"))
        if sale is not None:
            parts.append(f"<SalePrice>{esc(sale)}</SalePrice>")
        start = sku.get("SaleStartDate") or sku.get("special_from_time")
        end = sku.get("SaleEndDate") or sku.get("special_to_time")
        if start:
            parts.append(f"<SaleStartDate>{esc(start)}</SaleStartDate>")
        if end:
            parts.append(f"<SaleEndDate>{esc(end)}</SaleEndDate>")
        nodes.append("<Sku>" + "".join(parts) + "</Sku>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Request><Product><Skus>{''.join(nodes)}</Skus></Product></Request>"
    )


def extract_sku_prices_from_item(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map SellerSku → {price, special_price, ...} from GetProductItem data."""
    out: dict[str, dict[str, Any]] = {}
    skus = data.get("skus") if isinstance(data, dict) else None
    if not isinstance(skus, list):
        return out
    for sku in skus:
        if not isinstance(sku, dict):
            continue
        seller = sku.get("SellerSku") or sku.get("seller_sku")
        if not seller:
            continue
        out[str(seller)] = {
            "price": parse_money(sku.get("price")),
            "special_price": parse_money(sku.get("special_price")),
            "special_from_time": sku.get("special_from_time"),
            "special_to_time": sku.get("special_to_time"),
        }
    return out


def verify_pricing_fidelity(
    draft: dict[str, Any],
    live_item: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare draft vs live GetProductItem pricing (per SellerSku)."""
    if not live_item:
        return {
            "status": "PARTIAL",
            "regular_prices_complete": False,
            "special_prices_verified": False,
            "message": "No live item detail",
        }
    data = live_item.get("data") if isinstance(live_item.get("data"), dict) else live_item
    live_map = extract_sku_prices_from_item(data if isinstance(data, dict) else {})
    expected_special = 0
    matched_regular = 0
    matched_special = 0
    missing_live = 0
    for v in draft.get("variants") or []:
        if not isinstance(v, dict):
            continue
        seller = str(v.get("seller_sku") or "")
        live = live_map.get(seller)
        if not live:
            missing_live += 1
            continue
        reg = parse_money(v.get("price"))
        if reg is not None and live.get("price") is not None:
            if abs(float(live["price"]) - reg) < 0.011:
                matched_regular += 1
        sp = parse_money(v.get("special_price"))
        if sp is not None:
            expected_special += 1
            if live.get("special_price") is not None and abs(
                float(live["special_price"]) - sp
            ) < 0.011:
                matched_special += 1
    variants_n = len([v for v in (draft.get("variants") or []) if isinstance(v, dict)])
    if not live_map:
        return {
            "status": "PARTIAL",
            "variant_count": variants_n,
            "regular_prices_matched": 0,
            "special_prices_expected": expected_special,
            "special_prices_matched": 0,
            "regular_prices_complete": False,
            "special_prices_verified": expected_special == 0,
            "missing_live_skus": variants_n,
            "message": "Live item detail missing SKU pricing — deferred verify",
        }
    regular_ok = variants_n > 0 and matched_regular == variants_n and missing_live == 0
    special_ok = expected_special == 0 or matched_special == expected_special
    if regular_ok and special_ok and missing_live == 0:
        status = "MATCHED"
    elif matched_regular or matched_special or missing_live < variants_n:
        status = "PARTIAL"
    else:
        status = "PARTIAL" if missing_live else "FAILED"
    return {
        "status": status,
        "variant_count": variants_n,
        "regular_prices_matched": matched_regular,
        "special_prices_expected": expected_special,
        "special_prices_matched": matched_special,
        "regular_prices_complete": regular_ok,
        "special_prices_verified": special_ok,
        "missing_live_skus": missing_live,
    }
