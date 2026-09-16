"""Build ProductCloneDraft from a connected-store source product (Phase 4C)."""

from __future__ import annotations

from typing import Any, Literal

from src.brand_resolve import resolve_brand_for_category
from src.category_validate import validate_draft_against_category
from src.db.repo import get_repo
from src.description_enhance import enhance_description_with_images
from src.image_migrate import is_daraz_product_cdn_url
from src.package_resolve import resolve_variant_package
from src.product_create_payload import build_create_product_payload_preview
from src.seller_sku import DEFAULT_SKU_PREFIX, generate_seller_sku

SourceType = Literal["connected_store", "daraz_url", "community"]


def _product_images(product: dict[str, Any]) -> list[str]:
    images = product.get("images_json") or []
    urls: list[str] = []
    if isinstance(images, list):
        for item in images:
            if isinstance(item, dict) and item.get("url"):
                urls.append(str(item["url"]))
            elif isinstance(item, str):
                urls.append(item)
    return urls


def _image_strategy_for(urls: list[str]) -> dict[str, Any]:
    if not urls:
        return {"strategy": "none", "reuse_cdn": 0, "needs_migrate": 0}
    reuse = sum(1 for u in urls if is_daraz_product_cdn_url(u))
    needs = len(urls) - reuse
    if needs == 0:
        strategy = "reuse_cdn"
    elif reuse == 0:
        strategy = "migrate_all"
    else:
        strategy = "mixed"
    return {
        "strategy": strategy,
        "reuse_cdn": reuse,
        "needs_migrate": needs,
        "resolved_images": list(urls),  # CDN reuse; migrate applied at create time
    }


def build_connected_clone_draft(
    workspace_id: str,
    *,
    source_product_id: str,
    destination_store_id: str,
    category_attributes_payload: dict[str, Any] | None = None,
    brand_query_fn=None,
) -> dict[str, Any]:
    """Create a ProductCloneDraft for connected_store → connected_store.

    Quantity strategy: workspace ``default_initial_quantity`` (default 1),
    NOT live source stock.
    """
    repo = get_repo()
    product = repo.get_daraz_product(workspace_id, source_product_id)
    if not product:
        raise ValueError("Source product not found in this workspace")

    source_store = repo.get_store_by_uuid(workspace_id, str(product["store_id"]))
    if not source_store:
        raise ValueError("Source store not found in this workspace")

    dest = repo.get_store(workspace_id, destination_store_id) or repo.get_store_by_uuid(
        workspace_id, destination_store_id
    )
    if not dest:
        raise ValueError("Destination store not found in this workspace")

    if str(dest["id"]) == str(product["store_id"]):
        raise ValueError("Destination store must differ from source store")

    defaults = repo.get_product_defaults(workspace_id)
    prefix = defaults.get("sku_prefix") or DEFAULT_SKU_PREFIX
    initial_qty = int(defaults.get("default_initial_quantity") or 1)
    if initial_qty < 0:
        initial_qty = 0

    variants_src = repo.list_daraz_product_variants(workspace_id, source_product_id)
    existing_skus = repo.list_destination_seller_skus(workspace_id, str(dest["id"]))
    allocated: list[str] = list(existing_skus)

    warnings: list[str] = []
    errors: list[str] = []
    draft_variants: list[dict[str, Any]] = []

    title = product.get("title_en") or product.get("title") or "PRODUCT"
    for idx, v in enumerate(variants_src):
        sale_props = v.get("sale_props_json") or {}
        if not isinstance(sale_props, dict):
            sale_props = {}
        sku = generate_seller_sku(
            title=title,
            sale_props=sale_props,
            prefix=prefix,
            existing=allocated,
            index=idx,
        )
        allocated.append(sku)

        pkg = resolve_variant_package(
            source_type="connected_store",
            variant=v,
            defaults=defaults,
        )
        warnings.extend(pkg["warnings"])
        errors.extend(pkg["errors"])

        src_qty = v.get("quantity")
        if src_qty is not None:
            warnings.append(
                f"Source stock {src_qty} not copied for SKU {v.get('seller_sku')}; "
                f"using initial quantity {initial_qty}"
            )

        draft_variants.append(
            {
                "source_variant_id": v.get("id"),
                "source_seller_sku": v.get("seller_sku"),
                "source_daraz_sku_id": v.get("daraz_sku_id"),
                "sale_props": sale_props,
                "price": v.get("price"),
                "special_price": v.get("special_price"),
                "source_quantity": src_qty,
                "quantity": initial_qty,
                "seller_sku": sku,
                "package_weight": pkg["values"].get("package_weight"),
                "package_length": pkg["values"].get("package_length"),
                "package_width": pkg["values"].get("package_width"),
                "package_height": pkg["values"].get("package_height"),
                "package_sources": {
                    f: pkg["values"].get(f"{f}_source")
                    for f in (
                        "package_weight",
                        "package_length",
                        "package_width",
                        "package_height",
                    )
                },
                "images": v.get("images_json") or [],
            }
        )

    image_urls = _product_images(product)
    img_meta = _image_strategy_for(image_urls)
    desc_source = product.get("description_en") or product.get("description") or ""
    enhancement = enhance_description_with_images(desc_source, image_urls)

    duplicates = repo.find_possible_product_duplicates(
        workspace_id,
        str(dest["id"]),
        title=title,
        category_id=product.get("primary_category_id"),
    )

    will_copy = [
        "Title",
        "Category",
        "Attributes",
        "Variants",
        "Product Images",
        "Description",
    ]
    if product.get("package_content"):
        will_copy.append("Package Content")
    pkg_from_source = any(
        (dv.get("package_sources") or {}).get("package_weight") == "connected_source"
        for dv in draft_variants
    )
    if pkg_from_source:
        will_copy.append("Source Weight & Dimensions")
    elif draft_variants:
        will_copy.append("Package dimensions (workspace defaults where needed)")

    will_change = [
        "SellerSku → generated MTF-…",
        "Destination-specific item/SKU IDs (after create)",
        f"Quantity → workspace initial ({initial_qty})",
    ]
    not_copied = ["Video"]
    if product.get("video_ref"):
        warnings.append(
            "Video attached on source listing — automatic video cloning is not supported"
        )

    fidelity = {
        "copied": will_copy,
        "changed_by_multistore": will_change,
        "not_available": not_copied,
    }

    if duplicates:
        warnings.append(
            f"Possible duplicate(s) on destination: {len(duplicates)} match(es)"
        )

    # Brand resolution (optional live client; offline draft keeps source string)
    brand_resolution: dict[str, Any] = {
        "status": "PENDING",
        "brand": product.get("brand"),
        "message": "Brand resolution deferred until destination category query",
    }
    resolved_brand = product.get("brand")
    if brand_query_fn is not None:
        brand_resolution = resolve_brand_for_category(
            source_brand=product.get("brand"),
            primary_category_id=product.get("primary_category_id"),
            query_brands=brand_query_fn,
        )
        if brand_resolution.get("status") in {"EXACT_MATCH", "NO_BRAND"}:
            resolved_brand = brand_resolution.get("brand")
        elif brand_resolution.get("status") == "UNRESOLVED":
            errors.append(f"brand:{brand_resolution.get('message')}")

    draft = {
        "source_type": "connected_store",
        "source_product_id": str(product["id"]),
        "source_store_id": str(source_store.get("store_id")),
        "source_store_uuid": str(product["store_id"]),
        "source_store_name": source_store.get("display_name")
        or source_store.get("store_name")
        or source_store.get("store_id"),
        "source_daraz_item_id": product.get("daraz_item_id"),
        "destination_store_id": str(dest.get("store_id")),
        "destination_store_uuid": str(dest["id"]),
        "destination_store_name": dest.get("display_name")
        or dest.get("store_name")
        or dest.get("store_id"),
        "product": {
            "title": product.get("title"),
            "title_en": product.get("title_en"),
            "primary_category_id": product.get("primary_category_id"),
            "primary_category_name": product.get("primary_category_name"),
            "brand": resolved_brand,
            "attributes": {
                **(product.get("attributes_json") or {}),
                "brand": resolved_brand,
            },
            "variation": product.get("variation_json") or {},
            "description_html": enhancement["html"],
            "description_source_html": desc_source,
            "short_description_html": product.get("short_description_en")
            or product.get("short_description"),
            "package_content": product.get("package_content"),
            "warranty_type": (product.get("attributes_json") or {}).get("warranty_type"),
        },
        "media": {
            "product_images": image_urls,
            "resolved_images": img_meta.get("resolved_images") or image_urls,
            "image_strategy": img_meta,
            "market_images": [
                i.get("url") if isinstance(i, dict) else i
                for i in (product.get("market_images_json") or [])
            ],
            "description_enhancement": {
                "status": enhancement["enhancement"],
                "message": enhancement["message"],
                "appended_images": enhancement.get("appended_images") or [],
                "had_existing_images": enhancement.get("had_existing_images"),
            },
            "video": {
                "source_id": product.get("video_ref"),
                "status": "unsupported" if product.get("video_ref") else "none",
            },
            "migration_status": img_meta.get("strategy"),
        },
        "variants": draft_variants,
        "brand_resolution": brand_resolution,
        "possible_duplicates": [
            {
                "id": d.get("id"),
                "title": d.get("title"),
                "daraz_item_id": d.get("daraz_item_id"),
                "match_reason": d.get("match_reason"),
                "match_score": d.get("match_score"),
            }
            for d in duplicates[:8]
        ],
        "validation": {
            "missing_mandatory": list(errors),
            "unsupported": not_copied,
            "warnings": list(dict.fromkeys(warnings)),
            "errors": list(dict.fromkeys(errors)),
            "fidelity": fidelity,
            "can_create": False,  # set after category validation
            "create_gated": True,
            "category": None,
        },
    }

    category_result = None
    if category_attributes_payload is not None:
        category_result = validate_draft_against_category(draft, category_attributes_payload)
        draft["validation"]["category"] = category_result
        if not category_result["valid"]:
            for m in category_result["missing_required"]:
                errors.append(f"category_missing:{m}")
            for inv in category_result["invalid_values"]:
                errors.append(
                    f"category_invalid:{inv.get('attribute')}={inv.get('value')}"
                )
        draft["validation"]["errors"] = list(dict.fromkeys(errors))
        draft["validation"]["missing_mandatory"] = list(dict.fromkeys(errors))

    can_create = (
        len(draft["validation"]["errors"]) == 0
        and len(draft_variants) > 0
        and bool(image_urls)
        and (category_result is None or category_result.get("valid"))
        and not duplicates
        and brand_resolution.get("status") in {"EXACT_MATCH", "NO_BRAND"}
    )
    # PENDING brand OK for preview; create requires resolved brand
    if brand_resolution.get("status") == "PENDING" and category_result is None and not duplicates:
        # Preview-only readiness (create still gated)
        draft["validation"]["can_create"] = False
        draft["validation"]["preview_ready"] = (
            len(draft["validation"]["errors"]) == 0
            and len(draft_variants) > 0
            and bool(image_urls)
        )
    else:
        draft["validation"]["can_create"] = can_create
        draft["validation"]["preview_ready"] = can_create or (
            len(draft["validation"]["errors"]) == 0 and bool(image_urls)
        )

    return {
        "draft": draft,
        "fidelity": fidelity,
        "warnings": draft["validation"]["warnings"],
        "errors": draft["validation"]["errors"],
        "possible_duplicates": draft["possible_duplicates"],
        "preview": build_create_product_payload_preview(draft),
    }


# Re-export for callers that imported from product_clone
__all__ = [
    "build_connected_clone_draft",
    "build_create_product_payload_preview",
]
