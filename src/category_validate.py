"""Validate ProductCloneDraft against destination category mandatory attributes."""

from __future__ import annotations

from typing import Any


# SKU-level fields commonly marked mandatory on Daraz/Lazada categories
_SKU_FIELD_ALIASES = {
    "sellersku": "SellerSku",
    "seller_sku": "SellerSku",
    "price": "price",
    "package_weight": "package_weight",
    "package_length": "package_length",
    "package_width": "package_width",
    "package_height": "package_height",
    "quantity": "quantity",
}


def _truthy_mandatory(attr: dict[str, Any]) -> bool:
    raw = attr.get("is_mandatory")
    if raw in (1, "1", True, "true", "True"):
        return True
    return False


def parse_category_attributes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize /category/attributes/get response into a flat attribute list."""
    data = payload.get("data") if isinstance(payload.get("data"), (dict, list)) else payload
    attrs: list[Any]
    if isinstance(data, list):
        attrs = data
    elif isinstance(data, dict):
        attrs = (
            data.get("attributes")
            or data.get("attribute_list")
            or data.get("module")
            or []
        )
    else:
        attrs = []
    return [a for a in attrs if isinstance(a, dict)]


def _attr_name(attr: dict[str, Any]) -> str:
    return str(attr.get("name") or attr.get("attribute_name") or attr.get("label") or "").strip()


def _attr_type(attr: dict[str, Any]) -> str:
    return str(attr.get("attribute_type") or attr.get("attributeType") or "normal").lower()


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, dict)) and len(value) == 0:
        return False
    return True


def _draft_normal_value(draft: dict[str, Any], name: str) -> Any:
    product = draft.get("product") or {}
    attrs = product.get("attributes") or {}
    key = name.lower()
    # Direct attribute map
    for k, v in attrs.items():
        if str(k).lower() == key:
            return v
    # Well-known product fields
    mapping = {
        "name": product.get("title"),
        "name_en": product.get("title_en") or product.get("title"),
        "brand": product.get("brand") or attrs.get("brand"),
        "description": product.get("description_html"),
        "description_en": product.get("description_html"),
        "short_description": product.get("short_description_html"),
        "short_description_en": product.get("short_description_html"),
        "warranty_type": product.get("warranty_type") or attrs.get("warranty_type"),
        "package_content": product.get("package_content"),
    }
    if key in mapping:
        return mapping[key]
    return attrs.get(name)


def _draft_sku_values(draft: dict[str, Any], name: str) -> list[Any]:
    variants = draft.get("variants") or []
    key = name.lower()
    canonical = _SKU_FIELD_ALIASES.get(key, name)
    out: list[Any] = []
    for v in variants:
        if canonical == "SellerSku":
            out.append(v.get("seller_sku"))
        elif canonical == "price":
            out.append(v.get("price"))
        elif canonical in {
            "package_weight",
            "package_length",
            "package_width",
            "package_height",
            "quantity",
        }:
            out.append(v.get(canonical))
        else:
            # sale props / custom sku attrs
            props = v.get("sale_props") or {}
            if isinstance(props, dict):
                for pk, pv in props.items():
                    if str(pk).lower() == key:
                        out.append(pv)
                        break
                else:
                    out.append(None)
            else:
                out.append(None)
    return out


def _options_for(attr: dict[str, Any]) -> list[str]:
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


def validate_draft_against_category(
    draft: dict[str, Any],
    category_attributes_payload: dict[str, Any],
) -> dict[str, Any]:
    """Compare draft vs category mandatory attributes.

    Returns:
      valid, missing_required, invalid_values, mandatory_normal, mandatory_sku
    """
    attrs = parse_category_attributes(category_attributes_payload)
    missing: list[str] = []
    invalid: list[dict[str, Any]] = []
    mandatory_normal: list[str] = []
    mandatory_sku: list[str] = []

    for attr in attrs:
        if not _truthy_mandatory(attr):
            continue
        name = _attr_name(attr)
        if not name:
            continue
        atype = _attr_type(attr)
        options = _options_for(attr)

        if atype == "sku" or name.lower() in _SKU_FIELD_ALIASES:
            mandatory_sku.append(name)
            values = _draft_sku_values(draft, name)
            if not values or any(not _has_value(v) for v in values):
                missing.append(f"sku:{name}")
                continue
            if options:
                for v in values:
                    if _has_value(v) and str(v) not in options and str(v).lower() not in {
                        o.lower() for o in options
                    }:
                        invalid.append(
                            {
                                "attribute": name,
                                "scope": "sku",
                                "value": v,
                                "reason": "value_not_in_options",
                            }
                        )
        else:
            mandatory_normal.append(name)
            value = _draft_normal_value(draft, name)
            if not _has_value(value):
                missing.append(f"normal:{name}")
                continue
            if options and str(value) not in options and str(value).lower() not in {
                o.lower() for o in options
            }:
                # brand often resolved separately — soft invalid unless exact miss
                if name.lower() != "brand":
                    invalid.append(
                        {
                            "attribute": name,
                            "scope": "normal",
                            "value": value,
                            "reason": "value_not_in_options",
                        }
                    )

    # Always require at least one image + one variant at create time
    images = (draft.get("media") or {}).get("product_images") or []
    if not images:
        missing.append("media:product_images")
    variants = draft.get("variants") or []
    if not variants:
        missing.append("sku:variants")

    return {
        "valid": len(missing) == 0 and len(invalid) == 0,
        "missing_required": missing,
        "invalid_values": invalid,
        "mandatory_normal": mandatory_normal,
        "mandatory_sku": mandatory_sku,
    }
