"""Supervised CreateProduct path — explicit, env-gated, never auto-run."""

from __future__ import annotations

import os
from typing import Any

from src.brand_resolve import resolve_brand_for_category
from src.category_validate import validate_draft_against_category
from src.daraz_api import DarazApiError, DarazClient
from src.db.repo import get_repo
from src.image_migrate import DarazImageMigrationService
from src.product_clone import build_connected_clone_draft
from src.product_create_payload import (
    build_create_product_payload_preview,
    build_create_product_xml,
    redact_create_response,
)
from src.token_store import get_store as get_token_store


def create_probe_enabled() -> bool:
    return os.getenv("ALLOW_PRODUCT_CREATE_PROBE", "").lower() in {"1", "true", "yes"}


def product_create_enabled() -> bool:
    """True when live CreateProduct is allowed (full flag or supervised probe)."""
    full = os.getenv("ALLOW_PRODUCT_CREATE", "").lower() in {"1", "true", "yes"}
    return full or create_probe_enabled()


def run_supervised_create(
    workspace_id: str,
    *,
    source_product_id: str,
    destination_store_id: str,
    confirm: bool = False,
    allow_duplicates: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    """Prepare (and optionally execute) one supervised CreateProduct.

    Requires:
    - ALLOW_PRODUCT_CREATE_PROBE enabled
    - confirm=True
    - execute=True to actually call Daraz (otherwise dry-run validation + preview)
    - destination ≠ source
    - category + brand validation pass
    - no strong duplicates unless allow_duplicates=True (operator override)
    """
    if not create_probe_enabled():
        return {
            "status": "BLOCKED",
            "reason": "ALLOW_PRODUCT_CREATE_PROBE is not enabled",
        }
    if not confirm:
        return {
            "status": "BLOCKED",
            "reason": "confirm=true required for supervised create",
        }

    repo = get_repo()
    dest = repo.get_store(workspace_id, destination_store_id) or repo.get_store_by_uuid(
        workspace_id, destination_store_id
    )
    if not dest:
        return {"status": "BLOCKED", "reason": "Destination store not found"}

    token_record = get_token_store(str(dest.get("store_id")))
    if not token_record or not token_record.get("access_token"):
        return {
            "status": "BLOCKED",
            "reason": "Destination store has no usable access token in token store",
        }

    client = DarazClient(access_token=token_record["access_token"])

    # Build draft first (without live category) to know primary category
    base = build_connected_clone_draft(
        workspace_id,
        source_product_id=source_product_id,
        destination_store_id=destination_store_id,
    )
    draft = base["draft"]
    primary = (draft.get("product") or {}).get("primary_category_id")

    try:
        cat_payload = client.get_category_attributes(primary)
    except DarazApiError as exc:
        return {
            "status": "FAILED",
            "reason": f"category_attributes:{exc.code}:{exc}",
            "preview": build_create_product_payload_preview(draft),
        }

    brand_resolution = resolve_brand_for_category(
        source_brand=(draft.get("product") or {}).get("brand"),
        primary_category_id=primary,
        query_brands=client.query_category_brands,
    )
    if brand_resolution.get("status") in {"EXACT_MATCH", "NO_BRAND"}:
        draft["product"]["brand"] = brand_resolution.get("brand")
        attrs = draft["product"].get("attributes") or {}
        attrs["brand"] = brand_resolution.get("brand")
        draft["product"]["attributes"] = attrs
    draft["brand_resolution"] = brand_resolution

    category_result = validate_draft_against_category(draft, cat_payload)
    draft["validation"]["category"] = category_result

    errors: list[str] = list(draft["validation"].get("errors") or [])
    if brand_resolution.get("status") == "UNRESOLVED":
        errors.append(f"brand:{brand_resolution.get('message')}")
    if not category_result.get("valid"):
        errors.extend(f"category_missing:{m}" for m in category_result.get("missing_required") or [])
        errors.extend(
            f"category_invalid:{i.get('attribute')}"
            for i in category_result.get("invalid_values") or []
        )

    duplicates = draft.get("possible_duplicates") or []
    if duplicates and not allow_duplicates:
        return {
            "status": "NOT_RUN",
            "reason": (
                "Possible duplicate(s) on destination — refusing create. "
                "Pick a safe source/destination or pass allow_duplicates only with explicit operator intent."
            ),
            "duplicates": duplicates,
            "preview": build_create_product_payload_preview(draft),
            "validation": {**draft["validation"], "errors": errors},
            "brand_resolution": brand_resolution,
            "category": category_result,
        }

    # Resolve images (CDN reuse or migrate)
    media = draft.get("media") or {}
    source_images = list(media.get("product_images") or [])
    img_svc = DarazImageMigrationService(client)
    resolved = img_svc.resolve_many(source_images)
    final_urls: list[str] = []
    img_errors: list[str] = []
    strategies: list[str] = []
    for r in resolved:
        strategies.append(r.strategy)
        if r.status == "completed" and r.migrated_url:
            final_urls.append(r.migrated_url)
        else:
            img_errors.append(f"{r.source_url}:{r.status}:{r.error}")
    draft["media"]["resolved_images"] = final_urls
    draft["media"]["image_strategy"] = {
        "strategies": strategies,
        "resolved_count": len(final_urls),
        "errors": img_errors,
    }
    if img_errors or not final_urls:
        errors.append("images:unable_to_resolve_all")

    draft["validation"]["errors"] = list(dict.fromkeys(errors))
    draft["validation"]["can_create"] = (
        not draft["validation"]["errors"]
        and category_result.get("valid")
        and brand_resolution.get("status") in {"EXACT_MATCH", "NO_BRAND"}
        and bool(final_urls)
    )

    preview = build_create_product_payload_preview(draft)
    xml = build_create_product_xml(draft) if draft["validation"]["can_create"] else None

    if not draft["validation"]["can_create"]:
        return {
            "status": "NOT_RUN",
            "reason": "Validation failed — CreateProduct not submitted",
            "preview": preview,
            "validation": draft["validation"],
            "brand_resolution": brand_resolution,
            "category": category_result,
            "payload_xml_length": len(xml) if xml else 0,
        }

    if not execute:
        return {
            "status": "READY",
            "reason": "Validation passed; execute=false so CreateProduct was not called",
            "preview": preview,
            "validation": draft["validation"],
            "brand_resolution": brand_resolution,
            "category": category_result,
            "payload_xml_length": len(xml or ""),
            "mtf_skus": [v.get("seller_sku") for v in draft.get("variants") or []],
        }

    assert xml is not None
    try:
        resp = client.create_product(xml)
    except DarazApiError as exc:
        return {
            "status": "FAILED",
            "reason": f"CreateProduct:{exc.code}:{exc}",
            "daraz_error": redact_create_response(exc.payload),
            "preview": preview,
            "validation": draft["validation"],
            "mtf_skus": [v.get("seller_sku") for v in draft.get("variants") or []],
            "image_strategy": draft["media"].get("image_strategy"),
        }

    return {
        "status": "PROVEN",
        "daraz_response": redact_create_response(resp),
        "preview": preview,
        "validation": draft["validation"],
        "brand_resolution": brand_resolution,
        "category": category_result,
        "mtf_skus": [v.get("seller_sku") for v in draft.get("variants") or []],
        "image_strategy": draft["media"].get("image_strategy"),
        "source_product_id": source_product_id,
        "destination_store_id": str(dest.get("store_id")),
        "variant_count": len(draft.get("variants") or []),
    }
