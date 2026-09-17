"""One-click Add Daraz Product — fetch once, multi-destination create (gated)."""

from __future__ import annotations

import time
from typing import Any

from src.brand_resolve import resolve_brand_for_category
from src.category_validate import validate_draft_against_category
from src.daraz_api import DarazApiError, DarazClient
from src.db.repo import get_repo
from src.image_migrate import DarazImageMigrationService
from src.ops import client_for_store
from src.product_create import product_create_enabled
from src.product_create_payload import (
    build_create_product_payload_preview,
    build_create_product_xml,
    redact_create_response,
)
from src.product_fetch import ProductFetchError, fetch_connected_product
from src.product_clone import build_connected_clone_draft
from src.public_daraz import (
    PublicDarazError,
    build_public_clone_draft_from_extracted,
    fetch_public_product,
    find_connected_owner_for_item,
)


def _store_label(store: dict[str, Any] | None) -> dict[str, Any]:
    if not store:
        return {"store_id": None, "display_name": None}
    return {
        "store_id": store.get("store_id"),
        "display_name": store.get("display_name")
        or store.get("store_name")
        or store.get("store_id"),
        "id": store.get("id"),
    }


def _resolve_dest(workspace_id: str, store_ref: str) -> dict[str, Any] | None:
    repo = get_repo()
    return repo.get_store(workspace_id, store_ref) or repo.get_store_by_uuid(
        workspace_id, store_ref
    )


def _apply_price_override(draft: dict[str, Any], price_override: float | None) -> None:
    if price_override is None:
        return
    for v in draft.get("variants") or []:
        if isinstance(v, dict):
            v["price"] = price_override


def _variants_missing_price(draft: dict[str, Any]) -> bool:
    variants = draft.get("variants") or []
    if not variants:
        return True
    for v in variants:
        if not isinstance(v, dict):
            continue
        if v.get("price") is None and v.get("special_price") is None:
            return True
    return False


def _extract_item_id_from_create(resp: dict[str, Any] | None) -> str | None:
    if not isinstance(resp, dict):
        return None
    data = resp.get("data") if isinstance(resp.get("data"), dict) else resp
    if not isinstance(data, dict):
        return None
    for key in ("item_id", "itemId", "product_id", "productId"):
        if data.get(key) is not None:
            return str(data[key])
    return None


def _post_create_status(client: DarazClient, item_id: str | None) -> dict[str, Any]:
    if not item_id:
        return {"status": "Created", "item_id": None, "daraz_status": None}
    try:
        raw = client.get_product_item(item_id)
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        if not isinstance(data, dict):
            return {"status": "Created", "item_id": item_id, "daraz_status": None}
        status_raw = str(
            data.get("status") or data.get("product_status") or data.get("qcStatus") or ""
        )
        low = status_raw.lower()
        if "pending" in low or "qc" in low:
            mapped = "Pending QC"
        elif "active" in low:
            mapped = "Active"
        elif "fail" in low or "reject" in low:
            mapped = "Failed"
        else:
            mapped = "Created"
        return {
            "status": mapped,
            "item_id": item_id,
            "daraz_status": status_raw or None,
        }
    except Exception:  # noqa: BLE001
        return {"status": "Created", "item_id": item_id, "daraz_status": None}


def _prepare_destination(
    workspace_id: str,
    *,
    draft: dict[str, Any],
    dest: dict[str, Any],
    price_override: float | None,
    allow_duplicates: bool,
    execute: bool,
    confirm: bool,
    gate_on: bool,
) -> dict[str, Any]:
    """Validate one destination draft; optionally CreateProduct when gated+confirmed."""
    t0 = time.perf_counter()
    store_info = _store_label(dest)
    _apply_price_override(draft, price_override)

    if _variants_missing_price(draft) and price_override is None:
        return {
            "store": store_info,
            "status": "NEEDS_ATTENTION",
            "reason": "missing_price",
            "draft_preview": build_create_product_payload_preview(draft),
            "timings_ms": {"dest_total": round((time.perf_counter() - t0) * 1000, 1)},
        }

    duplicates = draft.get("possible_duplicates") or []
    if duplicates and not allow_duplicates:
        return {
            "store": store_info,
            "status": "POSSIBLE_DUPLICATE",
            "reason": f"{len(duplicates)} possible duplicate(s) on destination",
            "duplicates": duplicates[:5],
            "draft_preview": build_create_product_payload_preview(draft),
            "timings_ms": {"dest_total": round((time.perf_counter() - t0) * 1000, 1)},
        }

    try:
        client = client_for_store(dest)
    except Exception as exc:  # noqa: BLE001
        return {
            "store": store_info,
            "status": "NEEDS_ATTENTION",
            "reason": f"token:{exc}",
            "timings_ms": {"dest_total": round((time.perf_counter() - t0) * 1000, 1)},
        }

    primary = (draft.get("product") or {}).get("primary_category_id")
    errors: list[str] = list((draft.get("validation") or {}).get("errors") or [])
    category_result: dict[str, Any] | None = None
    brand_resolution = draft.get("brand_resolution") or {}

    if primary:
        try:
            cat_payload = client.get_category_attributes(primary)
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
            draft.setdefault("validation", {})["category"] = category_result
            if brand_resolution.get("status") == "UNRESOLVED":
                errors.append(f"brand:{brand_resolution.get('message')}")
            if not category_result.get("valid"):
                errors.extend(
                    f"category_missing:{m}"
                    for m in category_result.get("missing_required") or []
                )
                errors.extend(
                    f"category_invalid:{i.get('attribute')}"
                    for i in category_result.get("invalid_values") or []
                )
        except DarazApiError as exc:
            errors.append(f"category_attributes:{exc.code}:{exc}")
    else:
        errors.append("category:PrimaryCategory unresolved")

    media = draft.get("media") or {}
    source_images = list(media.get("product_images") or media.get("resolved_images") or [])
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
    draft.setdefault("media", {})["resolved_images"] = final_urls
    draft["media"]["image_strategy"] = {
        "strategies": strategies,
        "resolved_count": len(final_urls),
        "errors": img_errors,
    }
    if img_errors or not final_urls:
        errors.append("images:unable_to_resolve_all")

    draft.setdefault("validation", {})["errors"] = list(dict.fromkeys(errors))
    can_create = (
        not draft["validation"]["errors"]
        and (category_result is None or category_result.get("valid"))
        and brand_resolution.get("status") in {"EXACT_MATCH", "NO_BRAND", "PENDING"}
        and bool(final_urls)
        and bool(primary)
        and brand_resolution.get("status") != "UNRESOLVED"
        and brand_resolution.get("status") != "PENDING"
    )
    # PENDING brand without live resolve counts as not ready
    if brand_resolution.get("status") == "PENDING":
        can_create = False
        if "brand:unresolved_pending" not in errors:
            errors.append("brand:unresolved_pending")
            draft["validation"]["errors"] = list(dict.fromkeys(errors))

    draft["validation"]["can_create"] = can_create
    preview = build_create_product_payload_preview(draft)

    if not can_create:
        return {
            "store": store_info,
            "status": "NEEDS_ATTENTION",
            "reason": "; ".join(draft["validation"]["errors"][:4]) or "validation_failed",
            "validation": draft["validation"],
            "brand_resolution": brand_resolution,
            "category": category_result,
            "draft_preview": preview,
            "timings_ms": {"dest_total": round((time.perf_counter() - t0) * 1000, 1)},
        }

    if not gate_on:
        return {
            "store": store_info,
            "status": "READY",
            "reason": "Validation passed; create gated (ALLOW_PRODUCT_CREATE off)",
            "validation": draft["validation"],
            "draft_preview": preview,
            "timings_ms": {"dest_total": round((time.perf_counter() - t0) * 1000, 1)},
        }

    if not execute or not confirm:
        return {
            "store": store_info,
            "status": "READY",
            "reason": "Validation passed; execute/confirm required to create",
            "validation": draft["validation"],
            "draft_preview": preview,
            "timings_ms": {"dest_total": round((time.perf_counter() - t0) * 1000, 1)},
        }

    xml = build_create_product_xml(draft)
    try:
        resp = client.create_product(xml)
    except DarazApiError as exc:
        return {
            "store": store_info,
            "status": "Failed",
            "reason": f"CreateProduct:{exc.code}:{exc}",
            "daraz_error": redact_create_response(exc.payload),
            "draft_preview": preview,
            "timings_ms": {"dest_total": round((time.perf_counter() - t0) * 1000, 1)},
        }

    item_id = _extract_item_id_from_create(resp)
    post = _post_create_status(client, item_id)
    return {
        "store": store_info,
        "status": post["status"],
        "item_id": post.get("item_id"),
        "daraz_status": post.get("daraz_status"),
        "daraz_response": redact_create_response(resp),
        "draft_preview": preview,
        "timings_ms": {"dest_total": round((time.perf_counter() - t0) * 1000, 1)},
    }


def _summarize(destinations: list[dict[str, Any]], *, gate_on: bool, execute: bool) -> dict[str, Any]:
    created = sum(
        1
        for d in destinations
        if d.get("status") in {"Created", "Pending QC", "Active"}
    )
    failed = sum(1 for d in destinations if d.get("status") in {"Failed", "FAILED"})
    needs = sum(1 for d in destinations if d.get("status") == "NEEDS_ATTENTION")
    dupes = sum(1 for d in destinations if d.get("status") == "POSSIBLE_DUPLICATE")
    ready = sum(1 for d in destinations if d.get("status") == "READY")

    if not gate_on:
        top = "BLOCKED_CREATE"
    elif execute and created:
        top = "PARTIAL" if (failed or needs or dupes or ready) else "CREATED"
    elif execute and failed and not created:
        top = "FAILED"
    elif ready and not created:
        top = "READY" if gate_on else "BLOCKED_CREATE"
    elif needs and not ready and not created:
        top = "NEEDS_ATTENTION"
    else:
        top = "ANALYZED"

    return {
        "status": top,
        "created_count": created,
        "failed_count": failed,
        "needs_attention_count": needs,
        "duplicate_count": dupes,
        "ready_count": ready,
        "product_create_enabled": gate_on,
    }


def add_product_from_public_url(
    workspace_id: str,
    url: str,
    destination_store_ids: list[str],
    *,
    price_override: float | None = None,
    execute: bool = False,
    confirm: bool = False,
    allow_duplicates: bool = False,
    edit_before: bool = False,
) -> dict[str, Any]:
    """Fetch public URL once → prepare / optionally create on each destination."""
    t0 = time.perf_counter()
    dest_ids = [str(x).strip() for x in destination_store_ids if str(x).strip()]
    if not dest_ids:
        raise ValueError("destination_store_ids is required")

    gate_on = product_create_enabled()
    extracted = fetch_public_product(url)
    fetch_ms = float((extracted.get("timings_ms") or {}).get("fetch_extract") or 0)

    owner = find_connected_owner_for_item(workspace_id, extracted.get("item_id"))
    source_meta: dict[str, Any] = {
        "type": "public_daraz_url",
        "url": extracted.get("source_url"),
        "item_id": extracted.get("item_id"),
        "title": extracted.get("title"),
        "resolution": "public_daraz_url",
    }

    connected_product_id: str | None = None
    if owner and extracted.get("item_id"):
        fetched = fetch_connected_product(
            workspace_id,
            source_store_id=str(owner.get("store_id") or owner["id"]),
            daraz_item_id=str(extracted.get("item_id")),
        )
        connected_product_id = str(fetched["product"]["id"])
        source_meta["resolution"] = "connected_via_public_url"
        source_meta["source_store_id"] = owner.get("store_id")
        source_meta["title"] = (
            fetched["product"].get("title_en")
            or fetched["product"].get("title")
            or source_meta.get("title")
        )

    if edit_before:
        # Return first destination draft for advanced edit UI (no create).
        first = dest_ids[0]
        if connected_product_id:
            draft_result = build_connected_clone_draft(
                workspace_id,
                source_product_id=connected_product_id,
                destination_store_id=first,
            )
            draft = draft_result.get("draft") or {}
            draft["source_type"] = "connected_via_public_url"
            draft["source_url"] = extracted.get("source_url")
            draft_result["draft"] = draft
        else:
            draft_result = build_public_clone_draft_from_extracted(
                workspace_id,
                extracted=extracted,
                destination_store_id=first,
            )
        _apply_price_override(draft_result.get("draft") or {}, price_override)
        return {
            "status": "EDIT_BEFORE",
            "source": source_meta,
            "edit_before": True,
            "draft_result": draft_result,
            "destination_store_ids": dest_ids,
            "product_create_enabled": gate_on,
            "timings_ms": {
                "fetch_extract": fetch_ms,
                "total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    destinations: list[dict[str, Any]] = []
    for dest_ref in dest_ids:
        dest = _resolve_dest(workspace_id, dest_ref)
        if not dest:
            destinations.append(
                {
                    "store": {"store_id": dest_ref, "display_name": dest_ref},
                    "status": "NEEDS_ATTENTION",
                    "reason": "destination_store_not_found",
                }
            )
            continue
        try:
            if connected_product_id:
                draft_result = build_connected_clone_draft(
                    workspace_id,
                    source_product_id=connected_product_id,
                    destination_store_id=str(dest.get("store_id") or dest["id"]),
                )
                draft = draft_result.get("draft") or {}
                draft["source_type"] = "connected_via_public_url"
                draft["source_url"] = extracted.get("source_url")
            else:
                draft_result = build_public_clone_draft_from_extracted(
                    workspace_id,
                    extracted=extracted,
                    destination_store_id=str(dest.get("store_id") or dest["id"]),
                )
                draft = draft_result.get("draft") or {}
        except (PublicDarazError, ProductFetchError, ValueError) as exc:
            destinations.append(
                {
                    "store": _store_label(dest),
                    "status": "NEEDS_ATTENTION",
                    "reason": str(exc),
                }
            )
            continue

        destinations.append(
            _prepare_destination(
                workspace_id,
                draft=draft,
                dest=dest,
                price_override=price_override,
                allow_duplicates=allow_duplicates,
                execute=execute,
                confirm=confirm,
                gate_on=gate_on,
            )
        )

    summary = _summarize(destinations, gate_on=gate_on, execute=execute)
    # When gate off, never invent create success — promote READY to blocked top-level
    if not gate_on and summary["status"] not in {"NEEDS_ATTENTION", "FAILED"}:
        summary["status"] = "BLOCKED_CREATE"

    return {
        **summary,
        "source": source_meta,
        "destinations": destinations,
        "timings_ms": {
            "fetch_extract": fetch_ms,
            "total": round((time.perf_counter() - t0) * 1000, 1),
        },
    }


def add_product_from_connected(
    workspace_id: str,
    source_store_id: str,
    daraz_item_id: str,
    destination_store_ids: list[str],
    *,
    price_override: float | None = None,
    execute: bool = False,
    confirm: bool = False,
    allow_duplicates: bool = False,
    edit_before: bool = False,
) -> dict[str, Any]:
    """Fetch connected item once → prepare / optionally create on each destination."""
    t0 = time.perf_counter()
    dest_ids = [str(x).strip() for x in destination_store_ids if str(x).strip()]
    if not dest_ids:
        raise ValueError("destination_store_ids is required")

    gate_on = product_create_enabled()
    fetched = fetch_connected_product(
        workspace_id,
        source_store_id=source_store_id,
        daraz_item_id=daraz_item_id,
    )
    product = fetched["product"]
    product_id = str(product["id"])
    source_meta = {
        "type": "connected_store",
        "source_store_id": (fetched.get("source_store") or {}).get("store_id"),
        "item_id": product.get("daraz_item_id"),
        "title": product.get("title_en") or product.get("title"),
        "product_id": product_id,
    }

    if edit_before:
        first = dest_ids[0]
        draft_result = build_connected_clone_draft(
            workspace_id,
            source_product_id=product_id,
            destination_store_id=first,
        )
        _apply_price_override(draft_result.get("draft") or {}, price_override)
        return {
            "status": "EDIT_BEFORE",
            "source": source_meta,
            "edit_before": True,
            "draft_result": draft_result,
            "destination_store_ids": dest_ids,
            "product_create_enabled": gate_on,
            "timings_ms": {
                **(fetched.get("timings_ms") or {}),
                "total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    destinations: list[dict[str, Any]] = []
    for dest_ref in dest_ids:
        dest = _resolve_dest(workspace_id, dest_ref)
        if not dest:
            destinations.append(
                {
                    "store": {"store_id": dest_ref, "display_name": dest_ref},
                    "status": "NEEDS_ATTENTION",
                    "reason": "destination_store_not_found",
                }
            )
            continue
        # Skip same-store destination
        if str(dest["id"]) == str(product["store_id"]):
            destinations.append(
                {
                    "store": _store_label(dest),
                    "status": "NEEDS_ATTENTION",
                    "reason": "destination_must_differ_from_source",
                }
            )
            continue
        try:
            draft_result = build_connected_clone_draft(
                workspace_id,
                source_product_id=product_id,
                destination_store_id=str(dest.get("store_id") or dest["id"]),
            )
            draft = draft_result.get("draft") or {}
        except ValueError as exc:
            destinations.append(
                {
                    "store": _store_label(dest),
                    "status": "NEEDS_ATTENTION",
                    "reason": str(exc),
                }
            )
            continue

        destinations.append(
            _prepare_destination(
                workspace_id,
                draft=draft,
                dest=dest,
                price_override=price_override,
                allow_duplicates=allow_duplicates,
                execute=execute,
                confirm=confirm,
                gate_on=gate_on,
            )
        )

    summary = _summarize(destinations, gate_on=gate_on, execute=execute)
    if not gate_on and summary["status"] not in {"NEEDS_ATTENTION", "FAILED"}:
        summary["status"] = "BLOCKED_CREATE"

    return {
        **summary,
        "source": source_meta,
        "destinations": destinations,
        "timings_ms": {
            **(fetched.get("timings_ms") or {}),
            "total": round((time.perf_counter() - t0) * 1000, 1),
        },
    }
