"""Build CreateProduct XML payload and operator-safe redacted preview."""

from __future__ import annotations

import html
from typing import Any
from xml.sax.saxutils import escape

_KNOWN_SALE_PROP_KEYS = {
    "color", "size", "pack", "capacity", "model", "style", "type",
    "color_family", "size_family", "pack_size", "capacity_value",
    "model_name", "style_name", "type_name",
}


def _esc(value: Any) -> str:
    if value is None:
        return ""
    return escape(str(value), {"'": "&apos;", '"': "&quot;"})


def _cdata(value: Any) -> str:
    text = "" if value is None else str(value)
    # Avoid breaking CDATA; fall back to escaped text if needed
    if "]]>" in text:
        return _esc(text)
    return f"<![CDATA[{text}]]>"


def build_create_product_xml(draft: dict[str, Any]) -> str:
    """Construct Lazada/Daraz-style CreateProduct XML from a validated draft."""
    product = draft.get("product") or {}
    media = draft.get("media") or {}
    variants = draft.get("variants") or []
    images = list(media.get("resolved_images") or media.get("product_images") or [])[:8]

    attrs = dict(product.get("attributes") or {})
    # Ensure core fields
    attrs["name"] = product.get("title") or attrs.get("name") or ""
    attrs["name_en"] = product.get("title_en") or product.get("title") or attrs.get("name_en") or ""
    if product.get("brand"):
        attrs["brand"] = product.get("brand")
    # Lazada CreateProduct: brand deprecated; brand_id mandatory from GetBrandByPages.
    # We send brand_id when a verified id exists. brand name is retained for
    # compatibility with existing fixtures/docs that still accept both.
    if product.get("brand_id") is not None and str(product.get("brand_id")).strip() != "":
        attrs["brand_id"] = product.get("brand_id")
    if product.get("warranty_type"):
        attrs["warranty_type"] = product.get("warranty_type")
    if product.get("package_content"):
        attrs["package_content"] = product.get("package_content")
    desc = product.get("description_html") or ""
    short = product.get("short_description_html") or ""
    if desc:
        attrs["description_en"] = desc
        attrs.setdefault("description", desc)
    if short:
        attrs["short_description_en"] = short
        attrs.setdefault("short_description", short)

    # Drop empty / non-serializable
    attr_nodes: list[str] = []
    for key, value in attrs.items():
        if value is None or value == "":
            continue
        if isinstance(value, (dict, list)):
            continue
        # rich text fields use CDATA
        if str(key).lower() in {
            "description",
            "description_en",
            "short_description",
            "short_description_en",
        }:
            attr_nodes.append(f"<{_esc(key)}>{_cdata(value)}</{_esc(key)}>")
        else:
            attr_nodes.append(f"<{_esc(key)}>{_esc(value)}</{_esc(key)}>")

    image_nodes = "".join(f"<Image>{_esc(u)}</Image>" for u in images if u)

    sku_nodes: list[str] = []
    for v in variants:
        sale = v.get("sale_props") or {}
        mapping = (v.get("dimension_mapping") or {}).get("mapped") or []
        allowed_sale_keys = {
            str(m.get("destination_attribute_key"))
            for m in mapping
            if isinstance(m, dict) and m.get("destination_attribute_key")
        }
        # Source keys are provenance only. Serialize sale properties that the
        # category resolver explicitly mapped to destination attributes.
        if allowed_sale_keys:
            sale = {k: val for k, val in sale.items() if str(k) in allowed_sale_keys}
        else:
            sale = {
                k: val for k, val in sale.items()
                if str(k).strip().lower() in _KNOWN_SALE_PROP_KEYS
            }
        sale_xml = ""
        if isinstance(sale, dict) and sale:
            inner = "".join(
                f"<{_esc(k)}>{_esc(val)}</{_esc(k)}>" for k, val in sale.items() if val is not None
            )
            sale_xml = f"<saleProp>{inner}</saleProp>"
        sku_imgs = v.get("images") or []
        sku_img_urls: list[str] = []
        if isinstance(sku_imgs, list):
            for item in sku_imgs:
                if isinstance(item, dict) and item.get("url"):
                    sku_img_urls.append(str(item["url"]))
                elif isinstance(item, str):
                    sku_img_urls.append(item)
        sku_img_xml = ""
        if sku_img_urls:
            sku_img_xml = "<Images>" + "".join(
                f"<Image>{_esc(u)}</Image>" for u in sku_img_urls[:8]
            ) + "</Images>"

        parts = [
            f"<SellerSku>{_esc(v.get('seller_sku'))}</SellerSku>",
            f"<price>{_esc(v.get('price'))}</price>",
            f"<quantity>{_esc(v.get('quantity'))}</quantity>",
            f"<package_weight>{_esc(v.get('package_weight'))}</package_weight>",
            f"<package_length>{_esc(v.get('package_length'))}</package_length>",
            f"<package_width>{_esc(v.get('package_width'))}</package_width>",
            f"<package_height>{_esc(v.get('package_height'))}</package_height>",
        ]
        # Seller-configured SKU Special Price (not vouchers/campaigns).
        special = v.get("special_price")
        if special is not None and special != "":
            parts.append(f"<special_price>{_esc(special)}</special_price>")
            from_t = v.get("special_from_time") or v.get("special_from_date")
            to_t = v.get("special_to_time") or v.get("special_to_date")
            if not from_t or not to_t:
                from src.product_fidelity import default_special_window

                d_from, d_to = default_special_window()
                from_t = from_t or d_from
                to_t = to_t or d_to
            parts.append(f"<special_from_time>{_esc(from_t)}</special_from_time>")
            parts.append(f"<special_to_time>{_esc(to_t)}</special_to_time>")
        parts.extend([sale_xml, sku_img_xml])
        sku_nodes.append("<Sku>" + "".join(p for p in parts if p) + "</Sku>")

    primary = product.get("primary_category_id")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Request><Product>"
        f"<PrimaryCategory>{_esc(primary)}</PrimaryCategory>"
        f"<Images>{image_nodes}</Images>"
        f"<Attributes>{''.join(attr_nodes)}</Attributes>"
        f"<Skus>{''.join(sku_nodes)}</Skus>"
        "</Product></Request>"
    )
    return xml


def _field_provenance_rows(
    draft: dict[str, Any],
    *,
    price_override_report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Field-by-field list: value + origin (source / workspace default / override)."""
    product = draft.get("product") or {}
    media = draft.get("media") or {}
    brand_res = draft.get("brand_resolution") or {}
    rows: list[dict[str, Any]] = [
        {
            "field": "PrimaryCategory",
            "value": product.get("primary_category_id"),
            "origin": "source",
        },
        {
            "field": "Attributes.name",
            "value": product.get("title"),
            "origin": "source",
        },
        {
            "field": "Attributes.brand",
            "value": product.get("brand") or brand_res.get("brand"),
            "origin": "destination_no_brand_resolution",
        },
        {
            "field": "Attributes.brand_id",
            "value": product.get("brand_id") or brand_res.get("brand_id"),
            "origin": "destination_no_brand_resolution",
        },
        {
            "field": "brand_resolution.status",
            "value": brand_res.get("status"),
            "origin": "destination_no_brand_resolution",
        },
        {
            "field": "brand_resolution.uniqueness_verified",
            "value": bool(brand_res.get("uniqueness_verified")),
            "origin": "destination_no_brand_resolution",
        },
    ]
    images = list(media.get("resolved_images") or media.get("product_images") or [])
    for idx, url in enumerate(images[:8]):
        strategy = None
        strategies = (media.get("image_strategy") or {}).get("strategies") or []
        if idx < len(strategies):
            strategy = strategies[idx]
        rows.append(
            {
                "field": f"Images[{idx}]",
                "value": url,
                "origin": strategy or "source_or_cdn_reuse",
            }
        )

    override_keys = set()
    if isinstance(price_override_report, dict):
        for key in ("applied_keys", "variant_keys", "keys"):
            raw = price_override_report.get(key)
            if isinstance(raw, list):
                override_keys.update(str(x) for x in raw)

    for idx, v in enumerate(draft.get("variants") or []):
        if not isinstance(v, dict):
            continue
        sku = v.get("seller_sku")
        prefix = f"Skus[{idx}]"
        pkg = v.get("package_sources") or {}
        price_origin = str(v.get("price_source") or "source")
        if sku and str(sku) in override_keys:
            price_origin = "operator_override"
        elif price_origin in {"price_override", "variant_override"}:
            price_origin = "operator_override"
        rows.extend(
            [
                {
                    "field": f"{prefix}.SellerSku",
                    "value": sku,
                    "origin": "generated_mtf",
                },
                {
                    "field": f"{prefix}.price",
                    "value": v.get("price"),
                    "origin": price_origin,
                },
                {
                    "field": f"{prefix}.special_price",
                    "value": v.get("special_price"),
                    "origin": price_origin,
                },
                {
                    "field": f"{prefix}.quantity",
                    "value": v.get("quantity"),
                    "origin": (
                        "source"
                        if v.get("quantity_source") == "source"
                        else str(v.get("quantity_source") or "workspace_default")
                    ),
                },
                {
                    "field": f"{prefix}.package_weight",
                    "value": v.get("package_weight"),
                    "origin": str(pkg.get("package_weight") or "workspace_default"),
                },
                {
                    "field": f"{prefix}.package_length",
                    "value": v.get("package_length"),
                    "origin": str(pkg.get("package_length") or "workspace_default"),
                },
                {
                    "field": f"{prefix}.package_width",
                    "value": v.get("package_width"),
                    "origin": str(pkg.get("package_width") or "workspace_default"),
                },
                {
                    "field": f"{prefix}.package_height",
                    "value": v.get("package_height"),
                    "origin": str(pkg.get("package_height") or "workspace_default"),
                },
            ]
        )
    return rows


def build_create_product_payload_preview(
    draft: dict[str, Any],
    *,
    include_xml: bool = False,
    price_override_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Operator-safe redacted CreateProduct preview.

    When ``include_xml=True``, attaches the exact CreateProduct XML that would be
    sent (built locally — no Daraz calls, no tokens). Field provenance lists
    source vs workspace default vs operator override for key create fields.
    """
    product = draft.get("product") or {}
    variants = draft.get("variants") or []
    media = draft.get("media") or {}
    images = list(media.get("resolved_images") or media.get("product_images") or [])
    validation = draft.get("validation") or {}
    brand_res = draft.get("brand_resolution") or {}
    override_report = price_override_report or draft.get("price_override_report")

    skus = []
    for v in variants:
        if not isinstance(v, dict):
            continue
        skus.append(
            {
                "SellerSku": v.get("seller_sku"),
                "price": v.get("price"),
                "special_price": v.get("special_price"),
                "quantity": v.get("quantity"),
                "package_weight": v.get("package_weight"),
                "package_length": v.get("package_length"),
                "package_width": v.get("package_width"),
                "package_height": v.get("package_height"),
                "saleProp": v.get("sale_props"),
                "price_source": v.get("price_source"),
                "package_sources": v.get("package_sources"),
            }
        )

    desc_html = product.get("description_html") or ""
    out: dict[str, Any] = {
        "PrimaryCategory": product.get("primary_category_id"),
        "Attributes": {
            "name": product.get("title"),
            "name_en": product.get("title_en") or product.get("title"),
            "brand": product.get("brand"),
            "brand_id": product.get("brand_id"),
            "brand_resolution": brand_res.get("status"),
            "brand_uniqueness_verified": bool(brand_res.get("uniqueness_verified")),
            "warranty_type": product.get("warranty_type"),
            "package_content": product.get("package_content"),
            "description_en": f"[html redacted — length {len(desc_html)}]",
            "short_description_en": "[html redacted]",
        },
        "image_count": len(images),
        "Images": [u for u in images[:8]],
        "image_strategy": media.get("image_strategy"),
        "image_preflight": media.get("image_preflight"),
        "seller_item_limit": draft.get("seller_item_limit"),
        "variant_count": len(variants),
        "Skus": skus,
        "field_provenance": _field_provenance_rows(
            draft, price_override_report=override_report
        ),
        "validation_status": {
            "can_create": validation.get("can_create"),
            "category_valid": (validation.get("category") or {}).get("valid"),
            "missing_required": (validation.get("category") or {}).get("missing_required")
            or validation.get("missing_mandatory"),
            "brand_status": brand_res.get("status"),
            "brand_uniqueness_verified": bool(brand_res.get("uniqueness_verified")),
            "duplicate_count": len(draft.get("possible_duplicates") or []),
            "errors": validation.get("errors") or [],
            "warnings": validation.get("warnings") or [],
        },
        "notes": [
            "Preview only — live CreateProduct requires ALLOW_PRODUCT_CREATE_PROBE + supervised path",
            "Video not included",
            f"Description enhancement: {(media.get('description_enhancement') or {}).get('status')}",
            "Create blocked unless brand uniqueness_verified=true",
        ],
    }
    if include_xml:
        # Local XML only — never tokens/secrets. Safe for Copy diagnostics.
        try:
            out["create_product_xml"] = build_create_product_xml(draft)
        except Exception as exc:  # noqa: BLE001
            out["create_product_xml"] = None
            out["create_product_xml_error"] = type(exc).__name__
    return out


def redact_create_response(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Strip tokens / oversized bodies from Daraz create responses for reports."""
    if not payload:
        return {}
    keep: dict[str, Any] = {}
    for k in ("code", "request_id", "type", "message", "detail"):
        if k in payload:
            keep[k] = payload[k]
    data = payload.get("data")
    if isinstance(data, dict):
        safe_data = {}
        for k in (
            "item_id",
            "sku_list",
            "sku_id",
            "seller_sku",
            "shop_sku",
            "status",
            "qc_status",
        ):
            if k in data:
                safe_data[k] = data[k]
        # nested lists
        for k, v in data.items():
            if k in safe_data:
                continue
            if isinstance(v, list) and v and isinstance(v[0], dict):
                safe_data[k] = [
                    {
                        ik: iv
                        for ik, iv in item.items()
                        if ik.lower()
                        in {
                            "sku_id",
                            "seller_sku",
                            "shop_sku",
                            "item_id",
                            "status",
                        }
                    }
                    for item in v[:20]
                ]
        keep["data"] = safe_data
    return keep


def escape_for_report(text: str) -> str:
    return html.escape(text or "", quote=True)
