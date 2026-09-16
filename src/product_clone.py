"""Build ProductCloneDraft from a connected-store source product."""

from __future__ import annotations

from typing import Any, Literal

from src.db.repo import get_repo
from src.description_enhance import enhance_description_with_images
from src.package_resolve import resolve_variant_package
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


def build_connected_clone_draft(
    workspace_id: str,
    *,
    source_product_id: str,
    destination_store_id: str,
) -> dict[str, Any]:
    """Create a ProductCloneDraft for connected_store → connected_store.

    Quantity strategy (Phase 4A recommendation):
    Use workspace ``default_initial_quantity`` (default 1), NOT live source stock.
    Source quantity is shown informationally with a warning.
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

    can_create = len(errors) == 0 and len(draft_variants) > 0 and bool(image_urls)

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
            "brand": product.get("brand"),
            "attributes": product.get("attributes_json") or {},
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
            "migration_status": "pending",
        },
        "variants": draft_variants,
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
            "can_create": can_create,
            "create_gated": True,
        },
    }
    return {
        "draft": draft,
        "fidelity": fidelity,
        "warnings": draft["validation"]["warnings"],
        "errors": draft["validation"]["errors"],
        "possible_duplicates": draft["possible_duplicates"],
    }


def build_create_product_payload_preview(draft: dict[str, Any]) -> dict[str, Any]:
    """Redacted CreateProduct-oriented preview (XML not yet submitted).

    Used by gated probe endpoints. Does not call Daraz.
    """
    product = draft.get("product") or {}
    variants = draft.get("variants") or []
    images = (draft.get("media") or {}).get("product_images") or []
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
    return {
        "PrimaryCategory": product.get("primary_category_id"),
        "Attributes": {
            "name": product.get("title"),
            "name_en": product.get("title_en") or product.get("title"),
            "description_en": "[html redacted — length "
            f"{len(product.get('description_html') or '')}]",
            "short_description_en": "[html redacted]",
            "brand": product.get("brand"),
            "warranty_type": product.get("warranty_type"),
            "package_content": product.get("package_content"),
        },
        "Images": [u for u in images[:8]],
        "Skus": skus,
        "notes": [
            "Images must be Daraz-migrated URLs before live create",
            "Video not included",
            "Payload is preview-only unless ALLOW_PRODUCT_CREATE_PROBE=true",
        ],
    }
