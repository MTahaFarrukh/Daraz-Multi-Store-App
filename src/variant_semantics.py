"""Generic source-variant dimension semantics → destination SKU attributes.

Never map Pack/Size/Capacity values into color_family (or any unrelated attr).
"""

from __future__ import annotations

import re
import time
from typing import Any

# Semantic families: source property names / destination attr names normalize into these.
_SEMANTIC_GROUPS: dict[str, frozenset[str]] = {
    "color": frozenset(
        {
            "color",
            "colour",
            "colorfamily",
            "colourfamily",
            "color_family",
            "colour_family",
            "color family",
            "colour family",
            "maincolor",
            "maincolour",
        }
    ),
    "size": frozenset(
        {
            "size",
            "sizefamily",
            "size_family",
            "size family",
            "clothing_size",
            "clothingsize",
            "shoe_size",
            "shoesize",
        }
    ),
    "pack": frozenset(
        {
            "pack",
            "packsize",
            "pack_size",
            "pack size",
            "quantity",
            "qty",
            "piece",
            "pieces",
            "pcs",
            "set",
            "units",
            "unit",
            "numberofpieces",
            "number_of_pieces",
            "nopieces",
            "counts",
            "count",
            "multipack",
        }
    ),
    "capacity": frozenset(
        {
            "capacity",
            "volume",
            "netweight",
            "net_weight",
            "weight",
            "ml",
            "liter",
            "litre",
            "l",
            "storage",
            "storagecapacity",
        }
    ),
    "model": frozenset(
        {
            "model",
            "modelnumber",
            "model_number",
            "version",
            "edition",
        }
    ),
    "style": frozenset(
        {
            "style",
            "design",
            "pattern",
            "type",
            "flavour",
            "flavor",
            "variant",
            "option",
        }
    ),
}

_GREY_SYNONYMS = {"grey": "gray", "gray": "grey"}


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _norm_token(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def classify_property_semantic(
    property_name: str | None,
    *,
    property_id: str | None = None,
) -> dict[str, Any]:
    """Classify a source/destination property into a semantic type.

    Prefer property name (structured). property_id alone does not invent semantics.
    """
    raw = (property_name or "").strip()
    nk = _norm_key(raw)
    spaced = _norm_token(raw)
    for semantic, aliases in _SEMANTIC_GROUPS.items():
        if nk in {_norm_key(a) for a in aliases} or spaced in aliases:
            return {
                "semantic_type": semantic,
                "source_property_name": raw or None,
                "source_property_id": property_id,
                "confidence": "high" if raw else "low",
            }
    if not raw:
        return {
            "semantic_type": "unknown",
            "source_property_name": None,
            "source_property_id": property_id,
            "confidence": "none",
        }
    return {
        "semantic_type": "other",
        "source_property_name": raw,
        "source_property_id": property_id,
        "confidence": "medium",
    }


def classify_destination_attr(attr_name: str) -> str:
    info = classify_property_semantic(attr_name)
    return str(info["semantic_type"])


def dimensions_from_prop_path(
    prop_path: str | None,
    properties: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (sale_props flat map, dimensions[] with full provenance)."""
    if not prop_path or not isinstance(properties, list):
        if prop_path:
            return {"propPath": prop_path}, []
        return {}, []

    pid_meta: dict[str, dict[str, Any]] = {}
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        pid = str(prop.get("pid") or "")
        if not pid:
            continue
        pname = str(prop.get("name") or pid)
        values: dict[str, dict[str, Any]] = {}
        for val in prop.get("values") or []:
            if not isinstance(val, dict):
                continue
            vid = str(val.get("vid") if val.get("vid") is not None else "")
            if not vid:
                continue
            values[vid] = {
                "vid": vid,
                "name": str(val.get("name") or vid),
                "image": val.get("image"),
            }
        pid_meta[pid] = {"pid": pid, "name": pname, "values": values}

    sale: dict[str, Any] = {}
    dimensions: list[dict[str, Any]] = []
    for part in str(prop_path).split(";"):
        if ":" not in part:
            continue
        pid, vid = part.split(":", 1)
        meta = pid_meta.get(pid) or {}
        pname = str(meta.get("name") or pid)
        vmeta = (meta.get("values") or {}).get(vid) or {}
        vname = str(vmeta.get("name") or vid)
        sem = classify_property_semantic(pname, property_id=pid)
        sale[pname] = vname
        dimensions.append(
            {
                "source_property_id": pid,
                "source_property_name": pname,
                "source_value_id": vid,
                "source_value": vname,
                "semantic_type": sem["semantic_type"],
                "confidence": sem["confidence"],
                "image": vmeta.get("image"),
            }
        )
    if not sale and prop_path:
        return {"propPath": prop_path}, []
    return sale, dimensions


def dimensions_from_sale_props(sale_props: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Build dimensions from a flat sale_props map (when pid/vid unavailable)."""
    if not isinstance(sale_props, dict):
        return []
    out: list[dict[str, Any]] = []
    for k, v in sale_props.items():
        if v is None or str(v).strip() == "":
            continue
        if _norm_key(str(k)) == "proppath":
            continue
        sem = classify_property_semantic(str(k))
        out.append(
            {
                "source_property_id": None,
                "source_property_name": str(k),
                "source_value_id": None,
                "source_value": str(v).strip(),
                "semantic_type": sem["semantic_type"],
                "confidence": sem["confidence"],
            }
        )
    return out


def ensure_variant_dimensions(variant: dict[str, Any]) -> list[dict[str, Any]]:
    """Return dimensions list on variant, deriving from sale_props if needed."""
    dims = variant.get("dimensions")
    if isinstance(dims, list) and dims:
        return [d for d in dims if isinstance(d, dict)]
    sale = variant.get("sale_props") or {}
    dims = dimensions_from_sale_props(sale if isinstance(sale, dict) else {})
    variant["dimensions"] = dims
    return dims


def match_option_value(source: str, options: list[str]) -> str | None:
    """Map source value to a destination option when safely deterministic."""
    raw = (source or "").strip()
    if not raw:
        return None
    if not options:
        return raw

    sn = _norm_token(raw)
    for o in options:
        if _norm_token(o) == sn:
            return o

    alt = _GREY_SYNONYMS.get(sn)
    if alt:
        for o in options:
            if _norm_token(o) == alt:
                return o

    # Unique containment: "Light Purple" → "Purple" when only one option matches
    contained = [
        o
        for o in options
        if _norm_token(o) and _norm_token(o) in sn and _norm_token(o) != sn
    ]
    if contained:
        contained.sort(key=lambda x: len(_norm_token(x)), reverse=True)
        best = contained[0]
        best_n = _norm_token(best)
        peers = [o for o in contained if _norm_token(o) == best_n]
        if len(peers) == 1 and len(best_n) >= 3:
            return best

    # Pack-ish: "Pack of 2" ↔ "2" / "2 Pieces" / "2 Pcs"
    pack_num = re.search(
        r"(?:pack\s*of\s*)?(\d+)\s*(?:pcs|pc|pieces|piece|units?)?", sn
    )
    if pack_num:
        n = pack_num.group(1)
        hits = []
        for o in options:
            on = _norm_token(o)
            if on == n or on == f"{n} pcs" or on == f"{n} pieces" or on == f"{n} piece":
                hits.append(o)
            elif re.fullmatch(rf"pack\s*of\s*{n}", on):
                hits.append(o)
        if len(hits) == 1:
            return hits[0]

    return None


def find_destination_attr_for_semantic(
    semantic_type: str,
    sku_attrs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick the best destination SKU/sale attribute for a source semantic type."""
    if semantic_type in {"unknown", "other", ""}:
        return None
    candidates: list[tuple[int, dict[str, Any]]] = []
    for attr in sku_attrs:
        name = str(attr.get("name") or attr.get("attribute_name") or "").strip()
        if not name:
            continue
        dest_sem = classify_destination_attr(name)
        if dest_sem != semantic_type:
            continue
        score = 0
        if attr.get("is_mandatory") in (1, "1", True, "true", "True"):
            score += 10
        if _norm_key(name) in {
            "color_family",
            "size",
            "pack_size",
            "capacity",
            "model",
            "style",
        }:
            score += 5
        candidates.append((score, attr))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _options_list(attr: dict[str, Any]) -> list[str]:
    opts = attr.get("options") or attr.get("values") or attr.get("attribute_values") or []
    names: list[str] = []
    if isinstance(opts, list):
        for o in opts:
            if isinstance(o, dict):
                n = o.get("name") or o.get("en_name") or o.get("value")
                if n is not None:
                    names.append(str(n))
            elif o is not None:
                names.append(str(o))
    return names


def map_variant_dimensions_to_destination(
    variant: dict[str, Any],
    sku_attrs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Map all source dimensions onto destination sale_props keys.

    Does NOT invent mappings across semantic types.
    """
    t0 = time.perf_counter()
    dims = ensure_variant_dimensions(variant)
    sale = variant.setdefault("sale_props", {})
    if not isinstance(sale, dict):
        sale = {}
        variant["sale_props"] = sale

    for pk in list(sale.keys()):
        if _norm_key(str(pk)) == "proppath":
            del sale[pk]

    mapped: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    dest_by_name = {
        str(a.get("name") or a.get("attribute_name") or "").strip(): a
        for a in sku_attrs
        if str(a.get("name") or a.get("attribute_name") or "").strip()
    }

    for dim in dims:
        pname = str(dim.get("source_property_name") or "")
        pval = str(dim.get("source_value") or "").strip()
        if not pval:
            continue
        sem = str(dim.get("semantic_type") or "unknown")

        dest_attr = find_destination_attr_for_semantic(sem, sku_attrs)
        if dest_attr is None:
            for dname, dattr in dest_by_name.items():
                if _norm_key(dname) == _norm_key(pname):
                    dest_attr = dattr
                    break

        if dest_attr is None:
            unmapped.append({**dim, "reason": "no_compatible_destination_attribute"})
            continue

        dest_name = str(
            dest_attr.get("name") or dest_attr.get("attribute_name") or ""
        ).strip()
        options = _options_list(dest_attr)
        matched = match_option_value(pval, options) if options else pval
        if matched is None:
            unmapped.append(
                {
                    **dim,
                    "destination_attribute": dest_name,
                    "reason": "value_not_in_options",
                }
            )
            continue

        sale[dest_name] = matched
        if pname and pname != dest_name and pname in sale:
            del sale[pname]
        dest_sem = classify_destination_attr(dest_name)
        for pk in list(sale.keys()):
            if pk == dest_name:
                continue
            if (
                classify_destination_attr(str(pk)) == dest_sem == sem
                and sem not in {"unknown", "other"}
            ):
                del sale[pk]

        dim_out = dict(dim)
        dim_out.update(
            {
                "destination_attribute_key": dest_name,
                "destination_value": matched,
                "map_confidence": "high" if options else "medium",
            }
        )
        mapped.append(dim_out)

    # Strip sale_props keys that are source-only when we refused cross-semantic map
    # e.g. keep Pack in sale_props only if mapped to pack_size; if color_family was
    # wrongly present from older code, drop values that don't match any mapped dest.
    mapped_dest_keys = {m["destination_attribute_key"] for m in mapped}
    for pk in list(sale.keys()):
        if pk in mapped_dest_keys:
            continue
        # Drop keys whose semantic conflicts with a required dest we didn't map
        # Keep unmapped source keys that aren't color_family pollution
        if classify_destination_attr(str(pk)) == "color" and "color" not in {
            str(d.get("semantic_type")) for d in dims
        }:
            del sale[pk]

    variant["dimension_mapping"] = {"mapped": mapped, "unmapped": unmapped}
    return {
        "mapped": mapped,
        "unmapped": unmapped,
        "sale_props": sale,
        "timings_ms": {
            "variant_value_mapping_ms": round((time.perf_counter() - t0) * 1000, 1)
        },
    }


def collect_source_semantics(variants: list[dict[str, Any]]) -> list[str]:
    """Unique semantic types present across variants."""
    seen: list[str] = []
    for v in variants:
        for d in ensure_variant_dimensions(v):
            st = str(d.get("semantic_type") or "unknown")
            if st not in seen and st != "unknown":
                seen.append(st)
    return seen


def _human_attr(name: str) -> str:
    return name.replace("_", " ").strip() or name


def _human_source_sems(sems: list[str]) -> str:
    if not sems:
        return "no structured"
    labels = {
        "color": "Color",
        "size": "Size",
        "pack": "Pack",
        "capacity": "Capacity",
        "model": "Model",
        "style": "Style",
        "other": "Other",
    }
    return "/".join(labels.get(s, s) for s in sems)


def score_category_schema_compatibility(
    variants: list[dict[str, Any]],
    category_attributes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Score how well source variant dimensions fit destination SKU/sale attrs."""
    from src.category_validate import _attr_name, _attr_type, _truthy_mandatory

    sku_attrs: list[dict[str, Any]] = []
    for a in category_attributes:
        if not isinstance(a, dict):
            continue
        name = _attr_name(a)
        if not name:
            continue
        nk = _norm_key(name)
        if nk in {
            "sellersku",
            "price",
            "packageweight",
            "packagelength",
            "packagewidth",
            "packageheight",
            "quantity",
        }:
            continue
        if _attr_type(a) == "sku":
            sku_attrs.append(a)

    source_sems = collect_source_semantics(variants)
    dest_sems = [
        s
        for s in (classify_destination_attr(_attr_name(a)) for a in sku_attrs)
        if s not in {"unknown", "other"}
    ]

    matched = [s for s in source_sems if s in dest_sems]
    unmatched_source = [s for s in source_sems if s not in dest_sems]

    required_unsatisfied: list[dict[str, Any]] = []
    for a in sku_attrs:
        if not _truthy_mandatory(a):
            continue
        name = _attr_name(a)
        sem = classify_destination_attr(name)
        if sem in {"unknown", "other"}:
            continue
        if sem not in source_sems:
            required_unsatisfied.append(
                {
                    "attribute": name,
                    "semantic_type": sem,
                    "source_semantics": source_sems,
                    "message": (
                        f"Destination category requires {_human_attr(name)}, but the "
                        f"source product only contains a "
                        f"{_human_source_sems(source_sems)} variant."
                    ),
                }
            )

    compatible = not required_unsatisfied
    score = 1.0
    if source_sems:
        score = len(matched) / max(len(source_sems), 1)
    if required_unsatisfied:
        score = min(score, 0.2)

    return {
        "compatible": compatible,
        "score": round(score, 3),
        "source_semantics": source_sems,
        "destination_sale_semantics": dest_sems,
        "matched_semantics": matched,
        "unmatched_source_semantics": unmatched_source,
        "required_unsatisfied": required_unsatisfied,
        "sku_sale_attributes": [_attr_name(a) for a in sku_attrs],
    }


def resolve_all_variant_sale_props(
    draft: dict[str, Any],
    category_attributes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Map every variant's dimensions onto destination sale props."""
    t0 = time.perf_counter()
    from src.category_validate import _attr_name, _attr_type

    sku_attrs: list[dict[str, Any]] = []
    for a in category_attributes:
        if not isinstance(a, dict):
            continue
        name = _attr_name(a)
        if not name:
            continue
        nk = _norm_key(name)
        if nk in {
            "sellersku",
            "price",
            "packageweight",
            "packagelength",
            "packagewidth",
            "packageheight",
            "quantity",
        }:
            continue
        if _attr_type(a) == "sku":
            sku_attrs.append(a)

    variants = [v for v in (draft.get("variants") or []) if isinstance(v, dict)]
    per_variant: list[dict[str, Any]] = []
    map_ms = 0.0
    for v in variants:
        report = map_variant_dimensions_to_destination(v, sku_attrs)
        map_ms += float(
            (report.get("timings_ms") or {}).get("variant_value_mapping_ms") or 0
        )
        per_variant.append(report)

    compat = score_category_schema_compatibility(variants, category_attributes)
    return {
        "variants": per_variant,
        "compatibility": compat,
        "timings_ms": {
            "variant_semantic_resolution_ms": round(
                (time.perf_counter() - t0) * 1000, 1
            ),
            "variant_value_mapping_ms": round(map_ms, 1),
        },
    }
