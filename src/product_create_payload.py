"""Build CreateProduct XML payload and operator-safe redacted preview."""

from __future__ import annotations

import html
from typing import Any
from xml.sax.saxutils import escape


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
            sale_xml,
            sku_img_xml,
        ]
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


def build_create_product_payload_preview(draft: dict[str, Any]) -> dict[str, Any]:
    """Operator-safe redacted CreateProduct preview."""
    product = draft.get("product") or {}
    variants = draft.get("variants") or []
    media = draft.get("media") or {}
    images = list(media.get("resolved_images") or media.get("product_images") or [])
    validation = draft.get("validation") or {}
    brand_res = draft.get("brand_resolution") or {}

    skus = []
    for v in variants:
        skus.append(
            {
                "SellerSku": v.get("seller_sku"),
                "price": v.get("price"),
                "quantity": v.get("quantity"),
                "package_weight": v.get("package_weight"),
                "package_length": v.get("package_length"),
                "package_width": v.get("package_width"),
                "package_height": v.get("package_height"),
                "saleProp": v.get("sale_props"),
            }
        )

    desc_html = product.get("description_html") or ""
    return {
        "PrimaryCategory": product.get("primary_category_id"),
        "Attributes": {
            "name": product.get("title"),
            "name_en": product.get("title_en") or product.get("title"),
            "brand": product.get("brand"),
            "brand_resolution": brand_res.get("status"),
            "warranty_type": product.get("warranty_type"),
            "package_content": product.get("package_content"),
            "description_en": f"[html redacted — length {len(desc_html)}]",
            "short_description_en": "[html redacted]",
        },
        "image_count": len(images),
        "Images": [u for u in images[:8]],
        "image_strategy": media.get("image_strategy"),
        "variant_count": len(variants),
        "Skus": skus,
        "validation_status": {
            "can_create": validation.get("can_create"),
            "category_valid": (validation.get("category") or {}).get("valid"),
            "missing_required": (validation.get("category") or {}).get("missing_required")
            or validation.get("missing_mandatory"),
            "brand_status": brand_res.get("status"),
            "duplicate_count": len(draft.get("possible_duplicates") or []),
            "errors": validation.get("errors") or [],
            "warnings": validation.get("warnings") or [],
        },
        "notes": [
            "Preview only — live CreateProduct requires ALLOW_PRODUCT_CREATE_PROBE + supervised path",
            "Video not included",
            f"Description enhancement: {(media.get('description_enhancement') or {}).get('status')}",
        ],
    }


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
