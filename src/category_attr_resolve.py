"""Auto-resolve required Daraz category attributes from ProductCloneDraft source data.

NEEDS_ATTENTION only after deterministic resolution has been attempted.
Never invent product specs (material, gender, etc.).
"""

from __future__ import annotations

import html as html_lib
import re
import time
from typing import Any

from src.category_validate import (
    _attr_name,
    _attr_type,
    _options_for,
    _truthy_mandatory,
    parse_category_attributes,
)
from src.description_enhance import sanitize_description_html

# Short-lived cache: marketplace/category_id → (ts, raw attributes payload)
_CATEGORY_ATTR_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CATEGORY_ATTR_TTL_S = 60 * 60

# Product-level attribute aliases → draft lookup keys
_NORMAL_SOURCE_ALIASES: dict[str, tuple[str, ...]] = {
    "short_description": ("short_description", "short_description_en", "short_description_html"),
    "short_description_en": ("short_description_en", "short_description", "short_description_html"),
    "description": ("description", "description_en", "description_html"),
    "description_en": ("description_en", "description", "description_html"),
    "name": ("title", "name", "title_en"),
    "name_en": ("title_en", "title", "name_en", "name"),
    "package_content": ("package_content",),
    "warranty_type": ("warranty_type",),
}


def clear_category_attr_cache_for_tests() -> None:
    _CATEGORY_ATTR_CACHE.clear()


def get_cached_category_attributes(
    category_id: int | str | None,
    fetcher: Any,
    *,
    marketplace: str | None = None,
) -> dict[str, Any]:
    """Fetch category attributes with an in-process TTL cache keyed by marketplace/category."""
    mp = (marketplace or "pk").strip().lower() or "pk"
    key = f"{mp}/{category_id}" if category_id is not None else ""
    now = time.time()
    if key and key in _CATEGORY_ATTR_CACHE:
        ts, payload = _CATEGORY_ATTR_CACHE[key]
        if now - ts < _CATEGORY_ATTR_TTL_S:
            return payload
    t0 = time.perf_counter()
    payload = fetcher(category_id)
    fetch_ms = round((time.perf_counter() - t0) * 1000, 1)
    if key and isinstance(payload, dict):
        _CATEGORY_ATTR_CACHE[key] = (now, payload)
        payload = dict(payload)
        payload.setdefault("_cache_meta", {})
        if isinstance(payload["_cache_meta"], dict):
            payload["_cache_meta"]["category_attributes_ms"] = fetch_ms
            payload["_cache_meta"]["cache_key"] = key
    return payload


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _norm_token(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def html_to_plain_text(raw: str | None) -> str:
    text = raw or ""
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _attr_max_length(attr: dict[str, Any], *, default: int = 500) -> int:
    for key in ("max_length", "maxLength", "length", "input_length"):
        raw = attr.get(key)
        try:
            n = int(raw)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    advanced = attr.get("advanced")
    if isinstance(advanced, dict):
        for key in ("max_length", "maxLength", "length"):
            raw = advanced.get(key)
            try:
                n = int(raw)
                if n > 0:
                    return n
            except (TypeError, ValueError):
                pass
    return default


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if max_len <= 0 or len(text) <= max_len:
        return text
    cut = text[: max_len - 1].rstrip()
    # Prefer breaking on whitespace
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def _wrap_short_html(plain: str) -> str:
    plain = (plain or "").strip()
    if not plain:
        return ""
    # Prefer bullet list when multi-line content looks like highlights
    lines = [ln.strip(" •-\t") for ln in plain.splitlines() if ln.strip()]
    if len(lines) >= 2 and all(len(ln) < 180 for ln in lines[:8]):
        items = "".join(f"<li>{html_lib.escape(ln)}</li>" for ln in lines[:8])
        return f"<ul>{items}</ul>"
    return f"<p>{html_lib.escape(plain)}</p>"


def build_short_description(
    draft: dict[str, Any],
    *,
    attr: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic short_description from existing source fields."""
    product = draft.get("product") or {}
    max_len = _attr_max_length(attr or {}, default=500)

    candidates: list[tuple[str, str]] = []
    attrs_map = product.get("attributes") or {}
    for key, label in (
        ("short_description_html", "source_short_html"),
        ("short_description", "source_short"),
        ("short_description_en", "source_short_en"),
    ):
        raw = product.get(key) or attrs_map.get(key)
        if raw and str(raw).strip():
            candidates.append((str(raw), label))

    desc = product.get("description_html") or attrs_map.get("description")
    if desc and str(desc).strip():
        candidates.append((str(desc), "sanitized_description"))

    # Highlights / bullet lists when present on source extract
    highlights = (
        product.get("highlights")
        or attrs_map.get("highlights")
        or product.get("bullets")
        or attrs_map.get("bullets")
    )
    if isinstance(highlights, (list, tuple)):
        joined = "\n".join(str(h).strip() for h in highlights if h and str(h).strip())
        if joined:
            candidates.append((joined, "highlights"))
    elif highlights and str(highlights).strip():
        candidates.append((str(highlights), "highlights"))

    title = product.get("title_en") or product.get("title")
    if title and str(title).strip():
        candidates.append((str(title), "title_fallback"))

    for raw, source in candidates:
        if source.startswith("source_short") and "<" not in raw:
            plain = html_to_plain_text(raw) or raw.strip()
            html = _wrap_short_html(_truncate(plain, max_len))
        elif source in {"sanitized_description", "highlights"}:
            sanitized = sanitize_description_html(raw) if "<" in raw else raw
            plain = html_to_plain_text(sanitized) if "<" in str(sanitized) else str(
                sanitized
            ).strip()
            if not plain:
                continue
            html = _wrap_short_html(_truncate(plain, max_len))
        elif source == "title_fallback":
            html = _wrap_short_html(_truncate(str(title).strip(), max_len))
        else:
            sanitized = sanitize_description_html(raw)
            plain = html_to_plain_text(sanitized) or html_to_plain_text(raw)
            if not plain:
                continue
            html = _wrap_short_html(_truncate(plain, max_len))
        if html:
            return {"value": html, "source": source, "ok": True}
    return {"value": None, "source": None, "ok": False}


def match_option_value(source: str, options: list[str]) -> str | None:
    """Map source text to a destination option when safely deterministic."""
    from src.variant_semantics import match_option_value as _match

    return _match(source, options)


def _set_product_attr(draft: dict[str, Any], name: str, value: Any) -> None:
    product = draft.setdefault("product", {})
    attrs = product.setdefault("attributes", {})
    attrs[name] = value
    key = name.lower()
    if key in {"short_description", "short_description_en"}:
        product["short_description_html"] = value
        # Keep both attribute names in sync when both required
        attrs["short_description"] = value
        attrs["short_description_en"] = value
    elif key in {"description", "description_en"}:
        if not product.get("description_html"):
            product["description_html"] = value
    elif key in {"name", "name_en"}:
        if key == "name" and not product.get("title"):
            product["title"] = value
        if key == "name_en" and not product.get("title_en"):
            product["title_en"] = value


def resolve_required_category_attributes(
    draft: dict[str, Any],
    category_attributes_payload: dict[str, Any],
) -> dict[str, Any]:
    """Fill required attributes in-place; return resolution report.

    SKU sale attributes are resolved via generic semantic mapping
    (Color→color_family, Pack→pack_size, …) — never cross-semantic.
    """
    from src.variant_semantics import (
        classify_destination_attr,
        resolve_all_variant_sale_props,
    )

    t0 = time.perf_counter()
    attrs = parse_category_attributes(category_attributes_payload)
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    skipped: list[str] = []

    product = draft.setdefault("product", {})
    product_attrs = product.setdefault("attributes", {})
    variants = [v for v in (draft.get("variants") or []) if isinstance(v, dict)]

    # Generic variant dimension → destination saleProp mapping (all dims)
    variant_map = resolve_all_variant_sale_props(draft, attrs)
    draft.setdefault("validation", {})["variant_semantics"] = {
        "compatibility": variant_map.get("compatibility"),
        "timings_ms": variant_map.get("timings_ms"),
    }
    compat = variant_map.get("compatibility") or {}
    for miss in compat.get("required_unsatisfied") or []:
        unresolved.append(
            {
                "attribute": miss.get("attribute"),
                "scope": "sku",
                "reason": "semantic_mismatch",
                "message": miss.get("message"),
                "source_semantics": miss.get("source_semantics"),
            }
        )

    by_name = {_attr_name(a): a for a in attrs if _attr_name(a)}

    # Record per-SKU mapped sale props as resolved (when value present)
    for idx, variant in enumerate(variants):
        sale = variant.get("sale_props") or {}
        mapping = (variant.get("dimension_mapping") or {}).get("mapped") or []
        for m in mapping:
            dest_key = m.get("destination_attribute_key")
            if dest_key and sale.get(dest_key) not in (None, ""):
                resolved.append(
                    {
                        "attribute": dest_key,
                        "scope": "sku",
                        "variant_index": idx,
                        "value": sale.get(dest_key),
                        "source": (
                            f"dimension:{m.get('source_property_name')}"
                            f"/{m.get('semantic_type')}"
                        ),
                        "semantic_type": m.get("semantic_type"),
                    }
                )
        for u in (variant.get("dimension_mapping") or {}).get("unmapped") or []:
            unresolved.append(
                {
                    "attribute": u.get("destination_attribute")
                    or u.get("source_property_name"),
                    "scope": "sku",
                    "variant_index": idx,
                    "source_value": u.get("source_value"),
                    "reason": u.get("reason") or "unmapped_dimension",
                    "semantic_type": u.get("semantic_type"),
                }
            )

    for attr in attrs:
        if not _truthy_mandatory(attr):
            continue
        name = _attr_name(attr)
        if not name:
            continue
        atype = _attr_type(attr)
        options = _options_for(attr)
        lname = name.lower()

        if atype == "sku" or lname in {
            "sellersku",
            "seller_sku",
            "price",
            "package_weight",
            "package_length",
            "package_width",
            "package_height",
            "quantity",
        }:
            if lname in {
                "sellersku",
                "seller_sku",
                "price",
                "package_weight",
                "package_length",
                "package_width",
                "package_height",
                "quantity",
            }:
                skipped.append(f"sku:{name}")
                continue

            # Sale-prop attribute: only accept values when already mapped
            # under this exact destination key (semantic mapper ran above).
            dest_sem = classify_destination_attr(name)
            for idx, variant in enumerate(variants):
                sale = variant.get("sale_props") or {}
                if not isinstance(sale, dict):
                    continue
                existing = None
                for pk, pv in sale.items():
                    if _norm_key(str(pk)) == _norm_key(name) and pv not in (None, ""):
                        existing = pv
                        break

                if existing is not None:
                    matched = (
                        match_option_value(str(existing), options)
                        if options
                        else str(existing)
                    )
                    if matched is None and options:
                        # Invalid value under this key — remove pollution
                        for pk in list(sale.keys()):
                            if _norm_key(str(pk)) == _norm_key(name):
                                del sale[pk]
                        unresolved.append(
                            {
                                "attribute": name,
                                "scope": "sku",
                                "variant_index": idx,
                                "source_value": existing,
                                "reason": "value_not_in_options",
                                "semantic_type": dest_sem,
                            }
                        )
                        continue
                    sale[name] = matched if matched is not None else existing
                    for pk in list(sale.keys()):
                        if pk != name and _norm_key(str(pk)) == _norm_key(name):
                            del sale[pk]
                    continue

                # Missing — only report if not already covered by semantic_mismatch
                already = any(
                    u.get("attribute") == name
                    and u.get("reason") == "semantic_mismatch"
                    for u in unresolved
                )
                if not already:
                    # Check if any source dim has matching semantic (map failed on value)
                    from src.variant_semantics import ensure_variant_dimensions

                    dims = ensure_variant_dimensions(variant)
                    has_sem = any(
                        str(d.get("semantic_type")) == dest_sem for d in dims
                    )
                    if has_sem:
                        unresolved.append(
                            {
                                "attribute": name,
                                "scope": "sku",
                                "variant_index": idx,
                                "reason": "value_not_mapped",
                                "semantic_type": dest_sem,
                            }
                        )
                    # else: semantic_mismatch already recorded at category level
            continue

        # ---- normal (product-level) attributes ----
        current = product_attrs.get(name)
        if current in (None, ""):
            if lname in {"short_description", "short_description_en"}:
                current = product.get("short_description_html")
            elif lname in {"description", "description_en"}:
                current = product.get("description_html")
            elif lname == "name":
                current = product.get("title")
            elif lname == "name_en":
                current = product.get("title_en") or product.get("title")
            elif lname == "brand":
                current = product.get("brand")

        if current not in (None, ""):
            skipped.append(f"normal:{name}")
            continue

        if lname in {"short_description", "short_description_en"}:
            built = build_short_description(draft, attr=attr)
            if built.get("ok") and built.get("value"):
                _set_product_attr(draft, name, built["value"])
                other = (
                    "short_description_en"
                    if lname == "short_description"
                    else "short_description"
                )
                if other in by_name and _truthy_mandatory(by_name[other]):
                    other_cur = (product.get("attributes") or {}).get(other) or product.get(
                        "short_description_html"
                    )
                    if other_cur in (None, ""):
                        _set_product_attr(draft, other, built["value"])
                        resolved.append(
                            {
                                "attribute": other,
                                "scope": "normal",
                                "value_preview": str(built["value"])[:80],
                                "source": built["source"],
                            }
                        )
                resolved.append(
                    {
                        "attribute": name,
                        "scope": "normal",
                        "value_preview": str(built["value"])[:80],
                        "source": built["source"],
                    }
                )
                continue
            unresolved.append(
                {
                    "attribute": name,
                    "scope": "normal",
                    "reason": "no_source_text",
                }
            )
            continue

        aliases = _NORMAL_SOURCE_ALIASES.get(lname, (lname,))
        found = None
        found_from = None
        for alias in aliases:
            if alias in {"title", "title_en"}:
                val = product.get(alias)
            elif alias.endswith("_html"):
                val = product.get(alias)
            else:
                val = product_attrs.get(alias) or product.get(alias)
            if val not in (None, ""):
                found = val
                found_from = alias
                break

        if found is not None:
            if options:
                matched = match_option_value(str(found), options)
                if matched is None:
                    unresolved.append(
                        {
                            "attribute": name,
                            "scope": "normal",
                            "source_value": found,
                            "reason": "value_not_in_options",
                        }
                    )
                    continue
                found = matched
            _set_product_attr(draft, name, found)
            resolved.append(
                {
                    "attribute": name,
                    "scope": "normal",
                    "value_preview": str(found)[:80],
                    "source": found_from or "attributes",
                }
            )
            continue

        default = attr.get("default_value") or attr.get("default")
        if default not in (None, ""):
            if options:
                matched = match_option_value(str(default), options)
                if matched is None:
                    unresolved.append(
                        {
                            "attribute": name,
                            "scope": "normal",
                            "reason": "default_not_in_options",
                        }
                    )
                    continue
                default = matched
            _set_product_attr(draft, name, default)
            resolved.append(
                {
                    "attribute": name,
                    "scope": "normal",
                    "value_preview": str(default)[:80],
                    "source": "category_default",
                }
            )
            continue

        unresolved.append(
            {
                "attribute": name,
                "scope": "normal",
                "reason": "requires_seller_input",
            }
        )

    timings = {
        "attribute_resolution_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    timings.update(variant_map.get("timings_ms") or {})

    return {
        "resolved": resolved,
        "unresolved": unresolved,
        "skipped_existing": skipped,
        "resolved_count": len(resolved),
        "unresolved_count": len(unresolved),
        "compatibility": compat,
        "timings_ms": timings,
    }
