"""One-click Add Daraz Product — fetch once, multi-destination create (gated)."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from typing import Any

logger = logging.getLogger(__name__)

from src.brand_resolve import resolve_brand_for_category
from src.category_attr_resolve import (
    get_cached_category_attributes,
    resolve_required_category_attributes,
)
from src.category_validate import validate_draft_against_category
from src.daraz_api import DarazApiError, DarazClient
from src.db.repo import get_repo
from src.image_migrate import DarazImageMigrationService
from src.ops import client_for_store
from src.product_clone import build_connected_clone_draft
from src.product_create import product_create_enabled
from src.product_create_payload import (
    build_create_product_payload_preview,
    build_create_product_xml,
    redact_create_response,
)
from src.product_fetch import ProductFetchError, fetch_connected_product
from src.product_fidelity import (
    apply_price_overrides,
    build_update_price_xml,
    classify_duplicate_matches,
    default_special_window,
    list_unresolved_price_variants,
    parse_money,
    validate_variant_pricing,
    verify_pricing_fidelity,
)
from src.product_sync import product_payload_from_daraz, variant_payloads_from_daraz
from src.public_daraz import (
    PublicDarazError,
    build_public_clone_draft_from_extracted,
    fetch_public_product,
    find_connected_owner_for_item,
)
from src.product_create_reconcile import (
    apply_attempt_seller_skus,
    build_generated_seller_skus,
    build_source_identity,
)
from src.product_add_trace import (
    ProductAddTrace,
    bind_trace,
    current_trace,
    reset_trace,
)

# Bounded destination create pipelines (override via env for ops/tests).
PRODUCT_DESTINATION_CONCURRENCY = max(
    1, int(os.environ.get("PRODUCT_DESTINATION_CONCURRENCY", "3") or "3")
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


def _copy_permission_denied_result(
    dest: dict[str, Any] | None, gate: dict[str, Any]
) -> dict[str, Any]:
    return {
        "status": "NEEDS_ATTENTION",
        "store": _store_label(dest),
        "reason": str(gate.get("reason") or "copy_permission_denied"),
        "code": str(gate.get("code") or "copy_permission_denied"),
        "copy_permission": gate,
        "item_id": None,
        "attempt_id": None,
    }


def _enforce_copy_permission(
    workspace_id: str,
    *,
    source_store: dict[str, Any] | None = None,
    destination_store: dict[str, Any] | None = None,
    source_workspace_id: str | None = None,
) -> dict[str, Any]:
    """Fail closed when trusted-connection copy_products is required but missing."""
    from src.workspace_connections import assert_product_copy_allowed

    return assert_product_copy_allowed(
        actor_workspace_id=workspace_id,
        source_workspace_id=source_workspace_id,
        source_store=source_store,
        destination_store=destination_store,
    )


def _apply_price_override(
    draft: dict[str, Any],
    price_override: float | None,
    variant_price_overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    return apply_price_overrides(
        draft,
        price_override=price_override,
        variant_price_overrides=variant_price_overrides,
    )


def _variants_missing_price(draft: dict[str, Any]) -> bool:
    return bool(list_unresolved_price_variants(draft))


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


def _local_catalog_upsert(
    workspace_id: str,
    dest: dict[str, Any],
    *,
    item_id: str,
    draft: dict[str, Any],
    live_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Immediately index a newly created product for fast duplicate checks."""
    repo = get_repo()
    store_uuid = str(dest.get("id") or "")
    product = draft.get("product") or {}
    try:
        if live_item and isinstance(live_item, dict):
            data = (
                live_item.get("data")
                if isinstance(live_item.get("data"), dict)
                else live_item
            )
            if isinstance(data, dict) and data.get("item_id") is not None:
                payload = product_payload_from_daraz(workspace_id, store_uuid, data)
                row = repo.upsert_daraz_product(payload)
                for vp in variant_payloads_from_daraz(
                    workspace_id, store_uuid, str(row["id"]), data
                ):
                    repo.upsert_daraz_product_variant(vp)
                return {"ok": True, "product_id": row.get("id"), "source": "live_item"}
        images = list((draft.get("media") or {}).get("resolved_images") or [])
        row = repo.upsert_daraz_product(
            {
                "workspace_id": workspace_id,
                "store_id": store_uuid,
                "daraz_item_id": str(item_id),
                "title": product.get("title"),
                "title_en": product.get("title_en") or product.get("title"),
                "primary_category_id": product.get("primary_category_id"),
                "primary_category_name": product.get("primary_category_name"),
                "brand": product.get("brand"),
                "status_raw": "Created",
                "images_json": [{"url": u, "kind": "product"} for u in images[:8]],
                "detail_complete": False,
            }
        )
        for v in draft.get("variants") or []:
            if not isinstance(v, dict):
                continue
            sku_id = v.get("source_daraz_sku_id") or v.get("seller_sku")
            if not sku_id:
                continue
            repo.upsert_daraz_product_variant(
                {
                    "workspace_id": workspace_id,
                    "store_id": store_uuid,
                    "product_id": str(row["id"]),
                    "daraz_sku_id": str(sku_id),
                    "seller_sku": v.get("seller_sku"),
                    "sale_props_json": v.get("sale_props") or {},
                    "price": v.get("price"),
                    "special_price": v.get("special_price"),
                    "quantity": v.get("quantity"),
                    "package_weight": v.get("package_weight"),
                    "package_length": v.get("package_length"),
                    "package_width": v.get("package_width"),
                    "package_height": v.get("package_height"),
                }
            )
        return {"ok": True, "product_id": row.get("id"), "source": "draft"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _apply_special_prices(
    client: DarazClient,
    draft: dict[str, Any],
) -> dict[str, Any]:
    """Write per-SKU special prices via UpdatePrice when draft has them."""
    t0 = time.perf_counter()
    skus: list[dict[str, Any]] = []
    for v in draft.get("variants") or []:
        if not isinstance(v, dict):
            continue
        special = parse_money(v.get("special_price"))
        if special is None:
            continue
        from_t = v.get("special_from_time") or v.get("special_from_date")
        to_t = v.get("special_to_time") or v.get("special_to_date")
        if not from_t or not to_t:
            d_from, d_to = default_special_window()
            from_t = from_t or d_from
            to_t = to_t or d_to
        skus.append(
            {
                "SellerSku": v.get("seller_sku"),
                "Price": v.get("price"),
                "SalePrice": special,
                "SaleStartDate": from_t,
                "SaleEndDate": to_t,
            }
        )
    if not skus:
        return {
            "attempted": False,
            "applied": 0,
            "ok": True,
            "timings_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    xml = build_update_price_xml(skus)
    try:
        resp = client.update_price(xml)
        return {
            "attempted": True,
            "applied": len(skus),
            "ok": True,
            "response": redact_create_response(resp),
            "timings_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except DarazApiError as exc:
        return {
            "attempted": True,
            "applied": 0,
            "ok": False,
            "error": f"{exc.code}:{exc}",
            "response": redact_create_response(exc.payload),
            "timings_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


def _post_create_status(client: DarazClient, item_id: str | None) -> dict[str, Any]:
    if not item_id:
        return {"status": "Created", "item_id": None, "daraz_status": None, "raw": None}
    try:
        raw = client.get_product_item(item_id)
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        if not isinstance(data, dict):
            return {
                "status": "Created",
                "item_id": item_id,
                "daraz_status": None,
                "raw": raw,
            }
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
            "raw": raw,
        }
    except Exception:  # noqa: BLE001
        return {
            "status": "Created",
            "item_id": item_id,
            "daraz_status": None,
            "raw": None,
        }


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
    variant_price_overrides: dict[str, float] | None = None,
    resume_item_id: str | None = None,
    forced_attempt_id: str | None = None,
    forced_seller_skus: Any = None,
    claim_gate: bool = True,
    allow_image_migrate: bool | None = None,
) -> dict[str, Any]:
    """Validate one destination draft; optionally CreateProduct when gated+confirmed.

    When ``claim_gate`` is True (default), acquires the durable copy claim for
    connected identities before CreateProduct. Bulk copy passes False after it
    already holds the claim. Forced retries skip the gate so they can continue
    an owned attempt without being blocked by their own uncertain row.

    Image migration is allowed only when ``allow_image_migrate`` is True.
    Default: True only when both ``gate_on`` and ``execute`` (live create path).
    Dry-run / validation never calls migrate_image / UpdatePrice / CreateProduct.
    """
    del confirm  # probe-only concept; kept for call-site compatibility
    if allow_image_migrate is None:
        allow_image_migrate = bool(gate_on and execute)
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    store_info = _store_label(dest)
    trace = current_trace()
    if trace:
        trace.stage(
            "destination_resolved",
            store_id=str(store_info.get("store_id") or ""),
            execute=bool(execute),
            gate_on=bool(gate_on),
        )

    source_store_meta: dict[str, Any] | None = None
    src_ws = draft.get("source_workspace_id")
    src_uuid = draft.get("source_store_uuid") or draft.get("source_store_id")
    if src_uuid:
        source_store_meta = get_repo().get_store_by_uuid(
            workspace_id, str(src_uuid)
        ) or {"workspace_id": src_ws, "id": src_uuid}
    elif src_ws:
        source_store_meta = {"workspace_id": src_ws}
    perm = _enforce_copy_permission(
        workspace_id,
        source_store=source_store_meta,
        destination_store=dest,
        source_workspace_id=str(src_ws) if src_ws else None,
    )
    if not perm.get("allowed"):
        if trace:
            trace.stage(
                "copy_permission_denied",
                reason=perm.get("reason"),
                code=perm.get("code"),
            )
        return _copy_permission_denied_result(dest, perm)

    if forced_seller_skus is not None:
        apply_attempt_seller_skus(draft, forced_seller_skus)
    override_report = _apply_price_override(
        draft, price_override, variant_price_overrides
    )

    unresolved = list_unresolved_price_variants(draft)
    if unresolved:
        if trace:
            trace.stage(
                "validation_failed",
                store_id=str(store_info.get("store_id") or ""),
                reason="missing_price",
                status="NEEDS_ATTENTION",
            )
        return {
            "store": store_info,
            "status": "NEEDS_ATTENTION",
            "creation_status": "NEEDS_ATTENTION",
            "reason": "missing_price",
            "unresolved_variants": unresolved,
            "price_override_report": override_report,
            "draft_preview": build_create_product_payload_preview(draft),
            "timings_ms": {"dest_total": round((time.perf_counter() - t0) * 1000, 1)},
        }

    price_errors = validate_variant_pricing(draft)
    if price_errors:
        return {
            "store": store_info,
            "status": "NEEDS_ATTENTION",
            "creation_status": "NEEDS_ATTENTION",
            "reason": "; ".join(price_errors[:3]),
            "validation": {"errors": price_errors},
            "draft_preview": build_create_product_payload_preview(draft),
            "timings_ms": {"dest_total": round((time.perf_counter() - t0) * 1000, 1)},
        }

    t_dup = time.perf_counter()
    if draft.get("duplicate_check_done"):
        duplicates = list(draft.get("possible_duplicates") or [])
    else:
        repo = get_repo()
        product = draft.get("product") or {}
        found = repo.find_possible_product_duplicates(
            workspace_id,
            str(dest.get("id")),
            title=product.get("title") or "",
            category_id=product.get("primary_category_id"),
        )
        duplicates = [
            {
                "id": d.get("id"),
                "title": d.get("title"),
                "daraz_item_id": d.get("daraz_item_id"),
                "match_reason": d.get("match_reason"),
                "match_score": d.get("match_score"),
            }
            for d in found[:8]
        ]
        draft["possible_duplicates"] = duplicates
        draft["duplicate_check_done"] = True
    # Collect source dimensions for soft duplicate demotion (Pack of 1 vs 2, etc.)
    src_dims: list[dict[str, Any]] = []
    for v in draft.get("variants") or []:
        if isinstance(v, dict):
            for d in v.get("dimensions") or []:
                if isinstance(d, dict):
                    src_dims.append(d)
    dup_kind, dup_best = classify_duplicate_matches(
        duplicates, source_dimensions=src_dims or None
    )
    timings["duplicate_check_ms"] = round((time.perf_counter() - t_dup) * 1000, 1)
    timings["duplicate_ms"] = timings["duplicate_check_ms"]
    timings["duplicate_check_count"] = 1 if draft.get("duplicate_check_done") else 0

    if dup_kind == "ALREADY_EXISTS" and not allow_duplicates:
        return {
            "store": store_info,
            "status": "ALREADY_EXISTS",
            "creation_status": "ALREADY_EXISTS",
            "reason": "Product already exists on destination (local catalog)",
            "existing_product_id": (dup_best or {}).get("id"),
            "existing_daraz_item_id": (dup_best or {}).get("daraz_item_id"),
            "duplicates": duplicates[:5],
            "draft_preview": build_create_product_payload_preview(draft),
            "timings_ms": {
                **timings,
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    if dup_kind == "POSSIBLE_DUPLICATE" and not allow_duplicates:
        return {
            "store": store_info,
            "status": "POSSIBLE_DUPLICATE",
            "creation_status": "POSSIBLE_DUPLICATE",
            "reason": f"{len(duplicates)} possible duplicate(s) on destination",
            "duplicates": duplicates[:5],
            "draft_preview": build_create_product_payload_preview(draft),
            "timings_ms": {
                **timings,
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    # Early identity checks (no claim held yet — claims acquired only after
    # validation passes, and never across Daraz HTTP).
    if execute and not forced_attempt_id and not resume_item_id:
        if claim_gate:
            from src.product_copy_mapping import (
                find_uncertain_create_attempt,
                get_verified_copy_mapping,
                resolve_connected_copy_identity,
            )

            early_identity = resolve_connected_copy_identity(workspace_id, draft)
            if early_identity and dest.get("id"):
                early_mapping = get_verified_copy_mapping(
                    workspace_id,
                    source_store_id=early_identity["source_store_id"],
                    source_daraz_item_id=early_identity["source_daraz_item_id"],
                    destination_store_id=str(dest["id"]),
                )
                if early_mapping:
                    return {
                        "store": store_info,
                        "status": "ALREADY_EXISTS",
                        "creation_status": "VERIFIED",
                        "reason": "already_copied_to_destination",
                        "attempt_id": early_mapping.get("attempt_id"),
                        "item_id": early_mapping.get("destination_item_id"),
                        "existing_daraz_item_id": early_mapping.get(
                            "destination_item_id"
                        ),
                        "draft_preview": build_create_product_payload_preview(draft),
                        "timings_ms": {
                            **timings,
                            "dest_total": round(
                                (time.perf_counter() - t0) * 1000, 1
                            ),
                        },
                    }
                early_uncertain = find_uncertain_create_attempt(
                    workspace_id,
                    source_store_id=early_identity["source_store_id"],
                    source_daraz_item_id=early_identity["source_daraz_item_id"],
                    destination_store_id=str(dest["id"]),
                )
                if early_uncertain:
                    return {
                        "store": store_info,
                        "status": "NEEDS_ATTENTION",
                        "creation_status": "NEEDS_RECONCILIATION",
                        "reason": "product_create_attempt_requires_reconciliation",
                        "attempt_id": early_uncertain.get("id"),
                        "item_id": early_uncertain.get("destination_item_id"),
                        "draft_preview": build_create_product_payload_preview(draft),
                        "timings_ms": {
                            **timings,
                            "dest_total": round(
                                (time.perf_counter() - t0) * 1000, 1
                            ),
                        },
                    }

        # Pure public URL has no store-based mapping/claim. Still block re-create
        # when a prior attempt for the same source_identity→dest exists, because
        # request fingerprints include Seller SKUs that can change between runs.
        if str(draft.get("source_type") or "") == "public_daraz_url" and dest.get(
            "id"
        ):
            url_identity = build_source_identity(draft)
            if url_identity:
                repo_early = get_repo()
                prior_verified = repo_early.find_product_attempts_for_source_dest(
                    workspace_id,
                    source_identities=[url_identity],
                    destination_store_id=str(dest["id"]),
                    states=["VERIFIED"],
                )
                for prior in prior_verified:
                    dest_item = str(prior.get("destination_item_id") or "").strip()
                    if not dest_item:
                        continue
                    return {
                        "store": store_info,
                        "status": "ALREADY_EXISTS",
                        "creation_status": "VERIFIED",
                        "reason": "product_create_attempt_already_verified",
                        "attempt_id": prior.get("id"),
                        "item_id": dest_item,
                        "existing_daraz_item_id": dest_item,
                        "draft_preview": build_create_product_payload_preview(draft),
                        "timings_ms": {
                            **timings,
                            "dest_total": round(
                                (time.perf_counter() - t0) * 1000, 1
                            ),
                        },
                    }
                prior_uncertain = repo_early.find_product_attempts_for_source_dest(
                    workspace_id,
                    source_identities=[url_identity],
                    destination_store_id=str(dest["id"]),
                    states=[
                        "CREATING",
                        "CREATED_UNVERIFIED",
                        "NEEDS_RECONCILIATION",
                    ],
                )
                if prior_uncertain:
                    prior = prior_uncertain[0]
                    return {
                        "store": store_info,
                        "status": "NEEDS_ATTENTION",
                        "creation_status": "NEEDS_RECONCILIATION",
                        "reason": "product_create_attempt_requires_reconciliation",
                        "attempt_id": prior.get("id"),
                        "item_id": prior.get("destination_item_id"),
                        "draft_preview": build_create_product_payload_preview(draft),
                        "timings_ms": {
                            **timings,
                            "dest_total": round(
                                (time.perf_counter() - t0) * 1000, 1
                            ),
                        },
                    }

    try:
        client = client_for_store(dest)
    except Exception as exc:  # noqa: BLE001
        return {
            "store": store_info,
            "status": "NEEDS_ATTENTION",
            "creation_status": "NEEDS_ATTENTION",
            "reason": f"token:{exc}",
            "timings_ms": {
                **timings,
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    if resume_item_id:
        t_sp = time.perf_counter()
        special = _apply_special_prices(client, draft)
        timings["special_price_ms"] = float(
            special.get("timings_ms")
            or round((time.perf_counter() - t_sp) * 1000, 1)
        )
        post = _post_create_status(client, resume_item_id)
        fidelity = verify_pricing_fidelity(draft, post.get("raw"))
        _local_catalog_upsert(
            workspace_id,
            dest,
            item_id=str(resume_item_id),
            draft=draft,
            live_item=post.get("raw"),
        )
        status = post["status"] if special.get("ok") else "CREATED_WITH_WARNING"
        return {
            "store": store_info,
            "status": status,
            "creation_status": status,
            "item_id": resume_item_id,
            "reason": None
            if special.get("ok")
            else f"special_price_retry:{special.get('error')}",
            "pricing": {
                "special_prices_applied": special.get("applied") or 0,
                "special_prices_ok": bool(special.get("ok")),
            },
            "fidelity": fidelity,
            "retry_safe": {"item_id": resume_item_id, "step": "special_price"},
            "timings_ms": {
                **timings,
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    primary = (draft.get("product") or {}).get("primary_category_id")
    cat_res = draft.get("category_resolution") or {}
    if cat_res.get("conflict"):
        return {
            "store": store_info,
            "status": "NEEDS_ATTENTION",
            "creation_status": "NEEDS_ATTENTION",
            "reason": "category_evidence_conflict",
            "category_resolution": cat_res,
            "draft_preview": build_create_product_payload_preview(draft),
            "timings_ms": {
                **timings,
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }
    if not primary and cat_res.get("confidence") not in {"high", "medium"}:
        return {
            "store": store_info,
            "status": "NEEDS_ATTENTION",
            "creation_status": "NEEDS_ATTENTION",
            "reason": "category_unresolved",
            "category_resolution": cat_res,
            "draft_preview": build_create_product_payload_preview(draft),
            "timings_ms": {
                **timings,
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    errors: list[str] = [
        e
        for e in list((draft.get("validation") or {}).get("errors") or [])
        if e not in {"missing_variant_prices"}
    ]
    category_result: dict[str, Any] | None = None
    brand_resolution = draft.get("brand_resolution") or {}
    used_no_brand = bool(brand_resolution.get("used_no_brand"))

    if primary:
        t_cat = time.perf_counter()
        try:
            cat_payload = get_cached_category_attributes(
                primary,
                client.get_category_attributes,
                marketplace=str(dest.get("country") or "pk"),
            )
            timings["category_attributes_ms"] = round(
                (time.perf_counter() - t_cat) * 1000, 1
            )
            timings["category_resolution_ms"] = timings["category_attributes_ms"]
            timings["category_ms"] = timings["category_attributes_ms"]
            t_brand = time.perf_counter()
            prior_brand_meta = draft.get("brand_resolution") or {}
            source_brand_provenance = (
                prior_brand_meta.get("source_brand")
                or (draft.get("product") or {}).get("source_brand")
            )
            # Destination brand is always No Brand — never use draft.product.brand
            # as source (it may already be the No Brand placeholder).
            brand_resolution = resolve_brand_for_category(
                source_brand=source_brand_provenance,
                primary_category_id=primary,
                query_brands=client.query_category_brands,
                marketplace=str(dest.get("country") or "pk"),
            )
            timings["brand_resolution_ms"] = round(
                (time.perf_counter() - t_brand) * 1000, 1
            )
            timings["brand_ms"] = timings["brand_resolution_ms"]
            used_no_brand = bool(brand_resolution.get("used_no_brand")) or (
                brand_resolution.get("status") == "NO_BRAND"
            )
            if brand_resolution.get("status") == "NO_BRAND":
                draft["product"]["brand"] = brand_resolution.get("brand")
                attrs = draft["product"].get("attributes") or {}
                attrs["brand"] = brand_resolution.get("brand")
                draft["product"]["attributes"] = attrs
            # Always clear source brand from destination draft product field
            # when policy forces No Brand (provenance kept on brand_resolution).
            draft["brand_resolution"] = brand_resolution

            t_attr = time.perf_counter()
            attr_report = resolve_required_category_attributes(draft, cat_payload)
            timings["attribute_resolution_ms"] = float(
                (attr_report.get("timings_ms") or {}).get("attribute_resolution_ms")
                or round((time.perf_counter() - t_attr) * 1000, 1)
            )
            timings["attributes_ms"] = timings["attribute_resolution_ms"]
            for k in (
                "variant_semantic_resolution_ms",
                "variant_value_mapping_ms",
            ):
                if (attr_report.get("timings_ms") or {}).get(k) is not None:
                    timings[k] = float((attr_report.get("timings_ms") or {})[k])
            draft.setdefault("validation", {})["attribute_resolution"] = {
                "resolved_count": attr_report.get("resolved_count"),
                "unresolved_count": attr_report.get("unresolved_count"),
                "resolved": (attr_report.get("resolved") or [])[:20],
                "unresolved": (attr_report.get("unresolved") or [])[:20],
                "compatibility": attr_report.get("compatibility"),
            }

            category_result = validate_draft_against_category(draft, cat_payload)
            draft.setdefault("validation", {})["category"] = category_result
            if brand_resolution.get("status") == "UNRESOLVED":
                reason = brand_resolution.get("reason") or "brand_unresolved"
                errors.append(
                    f"brand:{reason}:{brand_resolution.get('message')}"
                )
            # Prefer clear semantic conflict messages over opaque category_invalid
            semantic_msgs = [
                u.get("message")
                for u in (attr_report.get("unresolved") or [])
                if u.get("reason") == "semantic_mismatch" and u.get("message")
            ]
            if semantic_msgs:
                for msg in dict.fromkeys(semantic_msgs):
                    errors.append(f"variant_schema_conflict:{msg}")
            if not category_result.get("valid"):
                covered_attrs = {
                    str(u.get("attribute"))
                    for u in (attr_report.get("unresolved") or [])
                    if u.get("reason") == "semantic_mismatch"
                }
                for m in category_result.get("missing_required") or []:
                    # sku:color_family → color_family
                    attr_bit = str(m).split(":")[-1]
                    if attr_bit in covered_attrs:
                        continue
                    errors.append(f"category_missing:{m}")
                for i in category_result.get("invalid_values") or []:
                    attr_name = str(i.get("attribute") or "")
                    if attr_name in covered_attrs:
                        continue
                    errors.append(f"category_invalid:{attr_name}")
            if (attr_report.get("compatibility") or {}).get("compatible") is False:
                draft.setdefault("validation", {})["category_schema_conflict"] = (
                    attr_report.get("compatibility")
                )
        except DarazApiError as exc:
            errors.append(f"category_attributes:{exc.code}:{exc}")
            timings.setdefault(
                "category_attributes_ms",
                round((time.perf_counter() - t_cat) * 1000, 1),
            )
            timings.setdefault(
                "category_resolution_ms",
                timings.get("category_attributes_ms", 0),
            )
    else:
        errors.append("category:PrimaryCategory unresolved")

    # Skip expensive image migration when category/brand already blocks create.
    blocking_pre_image = bool(
        any(
            str(e).startswith(
                (
                    "category_missing:",
                    "category_invalid:",
                    "category_attributes:",
                    "category:",
                    "brand:",
                    "variant_schema_conflict:",
                )
            )
            for e in errors
        )
        or brand_resolution.get("status") == "UNRESOLVED"
        or (category_result is not None and not category_result.get("valid"))
        or not primary
    )
    if blocking_pre_image:
        draft.setdefault("validation", {})["errors"] = list(dict.fromkeys(errors))
        draft["validation"]["can_create"] = False
        draft.setdefault("media", {})["resolved_images"] = []
        draft["media"]["image_strategy"] = {
            "strategies": [],
            "resolved_count": 0,
            "errors": [],
            "skipped": "preflight_blocked",
        }
        preview = build_create_product_payload_preview(draft)
        return {
            "store": store_info,
            "status": "NEEDS_ATTENTION",
            "creation_status": "NEEDS_ATTENTION",
            "reason": "; ".join(draft["validation"]["errors"][:4])
            or "validation_failed",
            "validation": draft["validation"],
            "category": category_result,
            "brand_resolution": brand_resolution,
            "used_no_brand": used_no_brand,
            "category_resolution": draft.get("category_resolution")
            or {
                "category_id": primary,
                "confidence": cat_res.get("confidence"),
                "source": cat_res.get("source"),
            },
            "draft_preview": preview,
            "price_override_report": override_report,
            "variant_count": len(
                [v for v in (draft.get("variants") or []) if isinstance(v, dict)]
            ),
            "timings_ms": {
                **timings,
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    media = draft.get("media") or {}
    source_images = list(
        media.get("product_images") or media.get("resolved_images") or []
    )
    t_img = time.perf_counter()
    img_svc = DarazImageMigrationService(client)
    resolved = img_svc.resolve_many(
        source_images, allow_migrate=bool(allow_image_migrate)
    )
    timings["images_ms"] = round((time.perf_counter() - t_img) * 1000, 1)
    final_urls: list[str] = []
    img_errors: list[str] = []
    strategies: list[str] = []
    needs_migration: list[str] = []
    for r in resolved:
        strategies.append(r.strategy)
        if r.status == "completed" and r.migrated_url:
            final_urls.append(r.migrated_url)
        else:
            img_errors.append(f"{r.source_url}:{r.status}:{r.error}")
            if r.strategy == "migration_disabled":
                needs_migration.append(str(r.source_url))
    draft.setdefault("media", {})["resolved_images"] = final_urls
    draft["media"]["image_strategy"] = {
        "strategies": strategies,
        "resolved_count": len(final_urls),
        "errors": img_errors,
        "unique_source_urls": len(dict.fromkeys(u for u in source_images if u)),
        "allow_migrate": bool(allow_image_migrate),
        "needs_migration": needs_migration,
    }
    if needs_migration and not allow_image_migrate:
        errors.append("images:migration_required_for_non_cdn_urls")
    if img_errors or not final_urls:
        errors.append("images:unable_to_resolve_all")

    draft.setdefault("validation", {})["errors"] = list(dict.fromkeys(errors))
    can_create = (
        not draft["validation"]["errors"]
        and (category_result is None or category_result.get("valid"))
        and brand_resolution.get("status") == "NO_BRAND"
        and bool(final_urls)
        and bool(primary)
    )
    draft["validation"]["can_create"] = can_create
    t_payload = time.perf_counter()
    preview = build_create_product_payload_preview(draft)
    timings["payload_build_ms"] = round((time.perf_counter() - t_payload) * 1000, 1)

    result_base = {
        "store": store_info,
        "category_resolution": draft.get("category_resolution")
        or {
            "category_id": primary,
            "confidence": cat_res.get("confidence"),
            "source": cat_res.get("source"),
        },
        "brand_resolution": brand_resolution,
        "used_no_brand": used_no_brand,
        "variant_count": len(
            [v for v in (draft.get("variants") or []) if isinstance(v, dict)]
        ),
        "price_override_report": override_report,
    }

    if not can_create:
        return {
            **result_base,
            "status": "NEEDS_ATTENTION",
            "creation_status": "NEEDS_ATTENTION",
            "reason": "; ".join(draft["validation"]["errors"][:4]) or "validation_failed",
            "validation": draft["validation"],
            "category": category_result,
            "draft_preview": preview,
            "timings_ms": {
                **timings,
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    if not gate_on:
        return {
            **result_base,
            "status": "READY",
            "creation_status": "READY",
            "reason": "Validation passed; create gated (ALLOW_PRODUCT_CREATE off)",
            "validation": draft["validation"],
            "draft_preview": preview,
            "timings_ms": {
                **timings,
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    if not execute:
        return {
            **result_base,
            "status": "READY",
            "creation_status": "READY",
            "reason": "Validation passed; dry-run (execute=false)",
            "validation": draft["validation"],
            "draft_preview": preview,
            "timings_ms": {
                **timings,
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    from src.product_copy_mapping import (
        finish_create_claim_after_outcome,
        gate_create_with_mapping_and_claim,
    )

    claim_id: str | None = None
    owner_token: str | None = None
    copy_identity: dict[str, str] | None = None
    # Bulk holds the claim outside; forced retries continue an owned attempt.
    use_claim_gate = bool(claim_gate) and not forced_attempt_id
    if use_claim_gate:
        gated = gate_create_with_mapping_and_claim(
            workspace_id, draft=draft, destination_store=dest
        )
        if gated["action"] in {"skip_verified", "block"}:
            out = gated.get("result") or {}
            return {
                **result_base,
                **{k: v for k, v in out.items() if k != "store"},
                "store": out.get("store") or store_info,
                "draft_preview": preview,
                "timings_ms": {
                    **timings,
                    "dest_total": round((time.perf_counter() - t0) * 1000, 1),
                },
            }
        claim_id = gated.get("claim_id")
        owner_token = gated.get("owner_token")
        copy_identity = gated.get("identity")

    def _finish_claim(
        outcome_status: str,
        *,
        destination_item_id: str | None = None,
        attempt_id: str | None = None,
    ) -> None:
        if not use_claim_gate:
            return
        try:
            finish_create_claim_after_outcome(
                workspace_id,
                claim_id=claim_id,
                owner_token=owner_token,
                identity=copy_identity,
                destination_store_id=str(dest.get("id") or ""),
                outcome_status=outcome_status,
                destination_item_id=destination_item_id,
                attempt_id=attempt_id,
            )
        except Exception:  # noqa: BLE001
            pass

    repo = get_repo()
    generated_rows = (
        build_generated_seller_skus(draft)
        if forced_seller_skus is None
        else build_generated_seller_skus(
            {**draft, "variants": draft.get("variants") or []}
        )
    )
    if forced_seller_skus is not None:
        # After apply_attempt_seller_skus, rebuild from draft so source ids attach
        generated_rows = build_generated_seller_skus(draft)
        # Prefer the exact persisted list (stable seller_sku values)
        from src.product_create_reconcile import normalize_generated_seller_skus

        forced_rows = normalize_generated_seller_skus(forced_seller_skus)
        if forced_rows:
            # Merge source ids from draft-derived rows when available
            by_sku = {r["seller_sku"]: r.get("source_daraz_sku_id") for r in generated_rows}
            generated_rows = [
                {
                    "seller_sku": r["seller_sku"],
                    "source_daraz_sku_id": r.get("source_daraz_sku_id")
                    or by_sku.get(r["seller_sku"]),
                }
                for r in forced_rows
            ]
    sku_values = [r["seller_sku"] for r in generated_rows]
    source_identity = build_source_identity(draft)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "source": source_identity,
                "store": str(dest.get("id")),
                "skus": sku_values,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if forced_attempt_id:
        attempt = repo.get_product_attempt(workspace_id, str(forced_attempt_id))
    else:
        attempt = repo.find_product_attempt(workspace_id, fingerprint)
    if attempt and attempt.get("state") == "VERIFIED":
        try:
            from src.product_copy_mapping import try_record_verified_from_attempt

            try_record_verified_from_attempt(
                workspace_id,
                attempt,
                source_store_id=str(
                    draft.get("source_store_uuid") or draft.get("source_store_id") or ""
                )
                or None,
                source_daraz_item_id=str(draft.get("source_daraz_item_id") or "")
                or None,
            )
        except Exception:  # noqa: BLE001
            pass
        _finish_claim(
            "VERIFIED",
            destination_item_id=str(attempt.get("destination_item_id") or "") or None,
            attempt_id=str(attempt["id"]),
        )
        return {
            **result_base,
            "status": "ALREADY_EXISTS",
            "creation_status": "VERIFIED",
            "reason": "product_create_attempt_already_verified",
            "attempt_id": attempt["id"],
            "item_id": attempt.get("destination_item_id"),
            "timings_ms": {
                **timings,
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }
    if attempt and attempt.get("state") in {
        "CREATING",
        "CREATED_UNVERIFIED",
        "NEEDS_RECONCILIATION",
    }:
        # Allow continuation only when this call owns the forced attempt (retry
        # already transitioned to CREATING). Otherwise block.
        if not (
            forced_attempt_id
            and str(attempt.get("id")) == str(forced_attempt_id)
            and attempt.get("state") == "CREATING"
        ):
            _finish_claim(
                "NEEDS_RECONCILIATION",
                destination_item_id=str(attempt.get("destination_item_id") or "")
                or None,
                attempt_id=str(attempt["id"]),
            )
            return {
                **result_base,
                "status": "NEEDS_ATTENTION",
                "creation_status": "NEEDS_RECONCILIATION",
                "reason": "product_create_attempt_requires_reconciliation",
                "attempt_id": attempt["id"],
                "timings_ms": {
                    **timings,
                    "dest_total": round((time.perf_counter() - t0) * 1000, 1),
                },
            }
    if not attempt:
        attempt = repo.create_product_attempt(
            {
                "workspace_id": workspace_id,
                "source_type": draft.get("source_type") or "unknown",
                "source_identity": source_identity,
                "destination_store_id": str(dest.get("id")),
                "request_fingerprint": fingerprint,
                "state": "PREPARING",
                "generated_seller_skus": generated_rows,
                "verification_state": "UNVERIFIED",
            }
        )
    else:
        # Keep exact SKUs on the attempt (never regenerate)
        repo.update_product_attempt(
            workspace_id,
            attempt["id"],
            generated_seller_skus=generated_rows,
        )
    repo.update_product_attempt(workspace_id, attempt["id"], state="CREATING")

    t_create = time.perf_counter()
    xml = build_create_product_xml(draft)
    if trace:
        trace.stage(
            "create_product_started",
            store_id=str(store_info.get("store_id") or ""),
            attempt_id=str(attempt.get("id") or ""),
        )
    try:
        resp = client.create_product(xml)
    except DarazApiError as exc:
        diag = getattr(exc, "diagnostics", None) or {}
        safe_bits = []
        if exc.http_status is not None:
            safe_bits.append(f"http={exc.http_status}")
        if diag.get("url_length") is not None:
            safe_bits.append(f"url_len={diag['url_length']}")
        if diag.get("body_length") is not None:
            safe_bits.append(f"body_len={diag['body_length']}")
        if diag.get("transport"):
            safe_bits.append(f"transport={diag['transport']}")
        suffix = f" ({', '.join(safe_bits)})" if safe_bits else ""
        timings["create_product_ms"] = round((time.perf_counter() - t_create) * 1000, 1)
        timings["create_ms"] = timings["create_product_ms"]
        uncertain = exc.http_status is None or exc.http_status >= 500
        fail_state = (
            "NEEDS_RECONCILIATION" if uncertain else "FAILED_SAFE_TO_RETRY"
        )
        repo.update_product_attempt(
            workspace_id,
            attempt["id"],
            state=fail_state,
            last_error=f"{exc.code}:{exc}",
            retry_count=int(attempt.get("retry_count") or 0) + 1,
        )
        _finish_claim(fail_state, attempt_id=str(attempt["id"]))
        if trace:
            trace.stage(
                "create_product_failed",
                store_id=str(store_info.get("store_id") or ""),
                status=fail_state,
                http_status=exc.http_status,
                error_class="DarazApiError",
                attempt_id=str(attempt.get("id") or ""),
            )
        return {
            **result_base,
            "status": "Failed",
            "creation_status": fail_state,
            "attempt_state": fail_state,
            "reason": f"CreateProduct:{exc.code}:{exc}{suffix}",
            "attempt_id": attempt["id"],
            "daraz_error": redact_create_response(exc.payload),
            "transport_diagnostics": {
                k: diag[k]
                for k in (
                    "http_status",
                    "content_type",
                    "url_length",
                    "body_length",
                    "transport",
                    "method",
                    "api_path",
                )
                if k in diag
            }
            if diag
            else None,
            "draft_preview": preview,
            "timings_ms": {
                **timings,
                "destination_total_ms": round((time.perf_counter() - t0) * 1000, 1),
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }
    except Exception:
        _finish_claim("NEEDS_RECONCILIATION", attempt_id=str(attempt["id"]))
        raise

    timings["create_product_ms"] = round((time.perf_counter() - t_create) * 1000, 1)
    timings["create_ms"] = timings["create_product_ms"]
    if trace:
        trace.stage(
            "create_product_response",
            store_id=str(store_info.get("store_id") or ""),
            attempt_id=str(attempt.get("id") or ""),
            status="CREATED_UNVERIFIED",
        )
    item_id = _extract_item_id_from_create(resp)
    repo.update_product_attempt(
        workspace_id,
        attempt["id"],
        state="CREATED_UNVERIFIED",
        destination_item_id=str(item_id) if item_id else None,
        daraz_response=redact_create_response(resp),
        generated_seller_skus=generated_rows,
    )

    t_sp = time.perf_counter()
    special = _apply_special_prices(client, draft)
    timings["special_price_ms"] = float(
        special.get("timings_ms") or round((time.perf_counter() - t_sp) * 1000, 1)
    )

    t_ver = time.perf_counter()
    # Prefer CreateProduct item_id for direct getProductItem — no product-list scan.
    post = _post_create_status(client, item_id)
    fidelity = verify_pricing_fidelity(draft, post.get("raw"))
    timings["verification_ms"] = round((time.perf_counter() - t_ver) * 1000, 1)
    mapping: dict[str, Any] = {}
    raw_item = post.get("raw") if isinstance(post, dict) else None
    if isinstance(raw_item, dict):
        from src.product_create_reconcile import _sku_mapping_from_product

        data = (
            raw_item.get("data")
            if isinstance(raw_item.get("data"), dict)
            else raw_item
        )
        if isinstance(data, dict):
            mapping = _sku_mapping_from_product(data, generated_rows)
    t_persist = time.perf_counter()
    verified = bool(post.get("raw")) and bool(item_id)
    # Missing destination item ID must never be treated as verified success.
    if not item_id:
        verified = False
    repo.update_product_attempt(
        workspace_id,
        attempt["id"],
        state="VERIFIED" if verified else "CREATED_UNVERIFIED",
        verification_state="VERIFIED" if verified else "UNVERIFIED",
        destination_item_id=str(item_id) if item_id else None,
        destination_sku_mapping=mapping or None,
    )
    if verified and item_id:
        try:
            from src.product_copy_mapping import try_record_verified_from_attempt

            refreshed = repo.get_product_attempt(workspace_id, attempt["id"]) or {
                **attempt,
                "state": "VERIFIED",
                "destination_item_id": str(item_id),
            }
            try_record_verified_from_attempt(
                workspace_id,
                refreshed,
                source_store_id=str(
                    draft.get("source_store_uuid") or draft.get("source_store_id") or ""
                )
                or None,
                source_daraz_item_id=str(draft.get("source_daraz_item_id") or "")
                or None,
            )
        except Exception:  # noqa: BLE001
            pass

    catalog_upsert: dict[str, Any] = {"ok": False}
    if item_id:
        catalog_upsert = _local_catalog_upsert(
            workspace_id,
            dest,
            item_id=str(item_id),
            draft=draft,
            live_item=post.get("raw"),
        )
    timings["persistence_ms"] = round((time.perf_counter() - t_persist) * 1000, 1)

    warnings: list[str] = []
    status = post["status"]
    creation_status = "CREATED"
    # Only treat hard pricing mismatch as warning — missing live SKUs is PARTIAL.
    if not item_id:
        status = "NEEDS_ATTENTION"
        creation_status = "CREATED_UNVERIFIED"
        warnings.append("missing_destination_item_id")
    elif (
        special.get("attempted")
        and not special.get("ok")
    ):
        status = "CREATED_WITH_WARNING"
        creation_status = "CREATED_WITH_WARNING"
        warnings.append(f"special_price:{special.get('error')}")
    elif fidelity.get("status") == "FAILED":
        status = "CREATED_WITH_WARNING"
        creation_status = "CREATED_WITH_WARNING"
        warnings.append("verification:pricing_mismatch")
    elif not catalog_upsert.get("ok"):
        warnings.append(f"catalog_upsert:{catalog_upsert.get('error')}")

    claim_outcome = (
        "VERIFIED"
        if verified and item_id
        else (
            "NEEDS_RECONCILIATION"
            if not item_id or not verified
            else str(creation_status)
        )
    )
    _finish_claim(
        claim_outcome,
        destination_item_id=str(item_id) if item_id else None,
        attempt_id=str(attempt["id"]),
    )

    special_detected = sum(
        1
        for v in (draft.get("variants") or [])
        if isinstance(v, dict) and parse_money(v.get("special_price")) is not None
    )

    dest_total = round((time.perf_counter() - t0) * 1000, 1)
    return {
        **result_base,
        "status": status,
        "creation_status": creation_status,
        "attempt_state": "VERIFIED" if verified else "CREATED_UNVERIFIED",
        "attempt_id": attempt["id"],
        "item_id": post.get("item_id") or item_id,
        "daraz_status": post.get("daraz_status"),
        "daraz_response": redact_create_response(resp),
        "draft_preview": preview,
        "pricing": {
            "regular_prices_complete": True,
            "special_prices_detected": special_detected,
            "special_prices_applied": special.get("applied") or 0,
            "special_prices_verified": bool(fidelity.get("special_prices_verified")),
            "special_price_write": (
                "update_price" if special.get("attempted") else "create_only"
            ),
        },
        "fidelity": fidelity,
        "warnings": warnings,
        "retry_safe": (
            {"item_id": item_id, "step": "special_price"}
            if item_id and creation_status == "CREATED_WITH_WARNING"
            else None
        ),
        "catalog_upsert": catalog_upsert,
        "timings_ms": {
            **timings,
            "destination_total_ms": dest_total,
            "dest_total": dest_total,
        },
    }


def _destination_concurrency() -> int:
    try:
        return max(1, int(os.environ.get("PRODUCT_DESTINATION_CONCURRENCY", "3") or "3"))
    except (TypeError, ValueError):
        return 3


def _prefetch_destination_context(
    workspace_id: str, dest_ids: list[str]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Batch workspace defaults + resolve stores + Seller SKUs once per request."""
    repo = get_repo()
    defaults = repo.get_product_defaults(workspace_id)
    stores: dict[str, dict[str, Any]] = {}
    skus_by_dest: dict[str, list[str]] = {}
    for dest_ref in dest_ids:
        dest = _resolve_dest(workspace_id, dest_ref)
        if not dest:
            continue
        stores[dest_ref] = dest
        dest_uuid = str(dest["id"])
        if dest_uuid not in skus_by_dest:
            skus_by_dest[dest_uuid] = list(
                repo.list_destination_seller_skus(workspace_id, dest_uuid)
            )
    return defaults, stores, skus_by_dest


def _refresh_destination_tokens(workspace_id: str, stores: list[dict[str, Any]]) -> None:
    """Coordinate token refresh once per store before concurrent destinations."""
    if not stores:
        return
    try:
        from src.token_refresh import refresh_store_tokens

        store_ids = [
            str(s.get("store_id") or s.get("id"))
            for s in stores
            if s.get("store_id") or s.get("id")
        ]
        if store_ids:
            refresh_store_tokens(
                store_ids=store_ids,
                within_minutes=15,
                workspace_id=workspace_id,
            )
    except Exception:  # noqa: BLE001
        # Soft — create path still surfaces token errors per destination.
        return


def _run_destinations_bounded(
    jobs: list[tuple[int, Any]],
    *,
    worker: Any,
    max_workers: int | None = None,
) -> dict[int, dict[str, Any]]:
    """Execute destination jobs with a concurrency cap.

    Returns ``{original_index: result}``. One job failure does not cancel siblings.
    """
    if not jobs:
        return {}
    workers = max(1, min(max_workers or _destination_concurrency(), len(jobs)))
    out: dict[int, dict[str, Any]] = {}

    def _safe(idx: int, payload: Any) -> tuple[int, dict[str, Any]]:
        try:
            return idx, worker(payload)
        except Exception as exc:  # noqa: BLE001
            return idx, {
                "status": "NEEDS_ATTENTION",
                "creation_status": "NEEDS_ATTENTION",
                "reason": f"destination_error:{type(exc).__name__}:{exc}",
            }

    if workers == 1 or len(jobs) == 1:
        for i, payload in jobs:
            idx, row = _safe(i, payload)
            out[idx] = row
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Preserve correlation ContextVar across worker threads.
            futs = [
                pool.submit(copy_context().run, _safe, i, payload)
                for i, payload in jobs
            ]
            for fut in as_completed(futs):
                idx, row = fut.result()
                out[idx] = row
    return out


def _summarize(
    destinations: list[dict[str, Any]], *, gate_on: bool, execute: bool
) -> dict[str, Any]:
    created = sum(
        1
        for d in destinations
        if d.get("status")
        in {"Created", "Pending QC", "Active", "CREATED_WITH_WARNING"}
        or d.get("creation_status") in {"CREATED", "CREATED_WITH_WARNING"}
    )
    failed = sum(1 for d in destinations if d.get("status") in {"Failed", "FAILED"})
    needs = sum(1 for d in destinations if d.get("status") == "NEEDS_ATTENTION")
    dupes = sum(
        1
        for d in destinations
        if d.get("status") in {"POSSIBLE_DUPLICATE", "ALREADY_EXISTS"}
    )
    ready = sum(1 for d in destinations if d.get("status") == "READY")
    already = sum(1 for d in destinations if d.get("status") == "ALREADY_EXISTS")

    if not gate_on:
        top = "BLOCKED_CREATE"
    elif execute and created:
        top = "PARTIAL" if (failed or needs or dupes or ready) else "CREATED"
    elif execute and failed and not created:
        top = "FAILED"
    elif already and not created and not failed and not needs:
        top = "ALREADY_EXISTS"
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
        "already_exists_count": already,
        "ready_count": ready,
        "product_create_enabled": gate_on,
    }


def _merge_timings(*parts: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        for k, v in part.items():
            key = str(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[key] = float(v)
                continue
            if isinstance(v, str) and key in {
                "catalog_strategy",
                "price_strategy",
            }:
                out[key] = v
                continue
            try:
                out[key] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def add_product_from_public_url(
    workspace_id: str,
    url: str,
    destination_store_ids: list[str],
    *,
    price_override: float | None = None,
    variant_price_overrides: dict[str, float] | None = None,
    execute: bool | None = None,
    confirm: bool = False,
    allow_duplicates: bool = False,
    edit_before: bool = False,
    resume_by_store: dict[str, str] | None = None,
    forced_attempt_id: str | None = None,
    forced_seller_skus: Any = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Fetch public URL once → prepare / create on each destination.

    When ``ALLOW_PRODUCT_CREATE=1`` (or probe flag), a normal Add Product call
    executes CreateProduct. Pass ``execute=False`` for dry-run validation only.
    ``confirm`` is ignored here (probe-only concept).
    """
    del confirm
    t0 = time.perf_counter()
    dest_ids = [str(x).strip() for x in destination_store_ids if str(x).strip()]
    if not dest_ids:
        raise ValueError("destination_store_ids is required")

    trace = ProductAddTrace.create(workspace_id)
    if correlation_id:
        trace.correlation_id = str(correlation_id)
    token = bind_trace(trace)
    try:
        return _add_product_from_public_url_inner(
            workspace_id,
            url,
            dest_ids,
            price_override=price_override,
            variant_price_overrides=variant_price_overrides,
            execute=execute,
            allow_duplicates=allow_duplicates,
            edit_before=edit_before,
            resume_by_store=resume_by_store,
            forced_attempt_id=forced_attempt_id,
            forced_seller_skus=forced_seller_skus,
            trace=trace,
            t0=t0,
        )
    finally:
        reset_trace(token)


def _add_product_from_public_url_inner(
    workspace_id: str,
    url: str,
    dest_ids: list[str],
    *,
    price_override: float | None,
    variant_price_overrides: dict[str, float] | None,
    execute: bool | None,
    allow_duplicates: bool,
    edit_before: bool,
    resume_by_store: dict[str, str] | None,
    forced_attempt_id: str | None,
    forced_seller_skus: Any,
    trace: ProductAddTrace,
    t0: float,
) -> dict[str, Any]:
    gate_on = product_create_enabled()
    if edit_before:
        will_execute = False
    elif not gate_on:
        will_execute = False
    elif execute is False:
        will_execute = False
    else:
        will_execute = True

    trace.stage(
        "gate_checked",
        gate_on=gate_on,
        execute=will_execute,
        edit_before=bool(edit_before),
        dest_count=len(dest_ids),
        source="public_url",
    )

    extracted = fetch_public_product(url)
    fetch_ms = float((extracted.get("timings_ms") or {}).get("fetch_extract") or 0)
    extract_timings = extracted.get("timings_ms") or {}

    owner = find_connected_owner_for_item(workspace_id, extracted.get("item_id"))
    source_meta: dict[str, Any] = {
        "type": "public_daraz_url",
        "url": extracted.get("source_url"),
        "item_id": extracted.get("item_id"),
        "title": extracted.get("title"),
        "resolution": "public_daraz_url",
        "pricing_summary": extracted.get("pricing_summary"),
        "category_resolution": extracted.get("category_resolution"),
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
        _apply_price_override(
            draft_result.get("draft") or {},
            price_override,
            variant_price_overrides,
        )
        trace.stage("edit_before_draft", dest_count=len(dest_ids), gate_on=gate_on)
        return {
            "status": "EDIT_BEFORE",
            "source": source_meta,
            "edit_before": True,
            "draft_result": draft_result,
            "destination_store_ids": dest_ids,
            "product_create_enabled": gate_on,
            "correlation_id": trace.correlation_id,
            "stage_history": trace.public_history(),
            "timings_ms": _merge_timings(
                extract_timings,
                {
                    "fetch_extract": fetch_ms,
                    "total": round((time.perf_counter() - t0) * 1000, 1),
                },
            ),
        }

    resume_map = {
        str(k): str(v)
        for k, v in (resume_by_store or {}).items()
        if k is not None and v is not None
    }
    defaults, store_map, skus_by_dest = _prefetch_destination_context(
        workspace_id, dest_ids
    )
    # Shared extracted payload must stay immutable across destinations.
    source_snapshot = copy.deepcopy(extracted)
    _refresh_destination_tokens(workspace_id, list(store_map.values()))
    # Re-resolve after refresh so access tokens are current.
    defaults, store_map, skus_by_dest = _prefetch_destination_context(
        workspace_id, dest_ids
    )

    jobs: list[tuple[int, dict[str, Any]]] = []
    placeholders: list[dict[str, Any] | None] = [None] * len(dest_ids)

    for idx, dest_ref in enumerate(dest_ids):
        dest = store_map.get(dest_ref)
        if not dest:
            placeholders[idx] = {
                "store": {"store_id": dest_ref, "display_name": dest_ref},
                "status": "NEEDS_ATTENTION",
                "reason": "destination_store_not_found",
            }
            continue
        jobs.append(
            (
                idx,
                {
                    "dest": dest,
                    "dest_ref": dest_ref,
                    "resume_id": resume_map.get(str(dest.get("store_id")))
                    or resume_map.get(str(dest.get("id"))),
                },
            )
        )

    def _one(payload: dict[str, Any]) -> dict[str, Any]:
        dest = payload["dest"]
        # Per-destination deepcopy so attribute mapping never mutates shared source.
        extracted_copy = copy.deepcopy(source_snapshot)
        try:
            if connected_product_id:
                draft_result = build_connected_clone_draft(
                    workspace_id,
                    source_product_id=connected_product_id,
                    destination_store_id=str(dest.get("store_id") or dest["id"]),
                    product_defaults=defaults,
                    existing_seller_skus=skus_by_dest.get(str(dest["id"])),
                    destination_store=dest,
                )
                draft = draft_result.get("draft") or {}
                draft["source_type"] = "connected_via_public_url"
                draft["source_url"] = extracted_copy.get("source_url")
            else:
                draft_result = build_public_clone_draft_from_extracted(
                    workspace_id,
                    extracted=extracted_copy,
                    destination_store_id=str(dest.get("store_id") or dest["id"]),
                    product_defaults=defaults,
                    existing_seller_skus=skus_by_dest.get(str(dest["id"])),
                    destination_store=dest,
                )
                draft = draft_result.get("draft") or {}
        except (PublicDarazError, ProductFetchError, ValueError) as exc:
            return {
                "store": _store_label(dest),
                "status": "NEEDS_ATTENTION",
                "reason": str(exc),
            }
        # Isolate draft mutations to this destination.
        draft = copy.deepcopy(draft)
        return _prepare_destination(
            workspace_id,
            draft=draft,
            dest=dest,
            price_override=price_override,
            variant_price_overrides=variant_price_overrides,
            allow_duplicates=allow_duplicates,
            execute=will_execute,
            confirm=True,
            gate_on=gate_on,
            resume_item_id=payload.get("resume_id"),
            forced_attempt_id=forced_attempt_id,
            forced_seller_skus=forced_seller_skus,
        )

    parallelism = min(_destination_concurrency(), max(1, len(jobs))) if jobs else 0
    ran = _run_destinations_bounded(jobs, worker=_one, max_workers=max(1, parallelism))
    destinations = []
    for idx in range(len(dest_ids)):
        if placeholders[idx] is not None:
            destinations.append(placeholders[idx] or {})
        else:
            destinations.append(ran.get(idx) or {"status": "NEEDS_ATTENTION"})

    summary = _summarize(destinations, gate_on=gate_on, execute=will_execute)
    if not gate_on and summary["status"] not in {"NEEDS_ATTENTION", "FAILED"}:
        summary["status"] = "BLOCKED_CREATE"

    for dest_row in destinations:
        if not isinstance(dest_row, dict):
            continue
        trace.stage(
            "destination_outcome",
            store_id=str((dest_row.get("store") or {}).get("store_id") or ""),
            status=str(dest_row.get("status") or ""),
            reason=str(dest_row.get("reason") or "")[:120] or None,
            attempt_id=str(dest_row.get("attempt_id") or "") or None,
        )

    trace.stage(
        "completed",
        status=str(summary.get("status") or ""),
        created_count=int(summary.get("created_count") or 0),
        failed_count=int(summary.get("failed_count") or 0),
        needs_attention_count=int(summary.get("needs_attention_count") or 0),
        elapsed_ms=trace.elapsed_ms(),
    )

    extract_timings = dict(extract_timings)
    return {
        **summary,
        "source": source_meta,
        "destinations": destinations,
        "correlation_id": trace.correlation_id,
        "stage_history": trace.public_history(),
        "timings_ms": _merge_timings(
            extract_timings,
            {
                "fetch_extract": fetch_ms,
                "pdp_fetch_ms": float(extract_timings.get("pdp_fetch_ms") or fetch_ms),
                "structured_parse_ms": float(
                    extract_timings.get("structured_parse_ms")
                    or extract_timings.get("parse_ms")
                    or 0
                ),
                "catalog_ms": float(extract_timings.get("catalog_ms") or 0),
                "price_resolution_ms": float(
                    extract_timings.get("price_resolution_ms") or 0
                ),
                "source_total_ms": float(
                    extract_timings.get("source_total_ms")
                    or extract_timings.get("fetch_extract")
                    or fetch_ms
                ),
                "catalog_strategy": extract_timings.get("catalog_strategy"),
                "price_strategy": extract_timings.get("price_strategy"),
                "destination_parallelism": parallelism if jobs else 0,
                "total_ms": round((time.perf_counter() - t0) * 1000, 1),
                "total": round((time.perf_counter() - t0) * 1000, 1),
            },
        ),
    }


def add_product_from_connected(
    workspace_id: str,
    source_store_id: str,
    daraz_item_id: str,
    destination_store_ids: list[str],
    *,
    price_override: float | None = None,
    variant_price_overrides: dict[str, float] | None = None,
    execute: bool | None = None,
    confirm: bool = False,
    allow_duplicates: bool = False,
    edit_before: bool = False,
    resume_by_store: dict[str, str] | None = None,
    forced_attempt_id: str | None = None,
    forced_seller_skus: Any = None,
    claim_gate: bool = True,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Fetch connected item once → prepare / create on each destination.

    ``claim_gate`` defaults True so single-product / URL-resolved connected
    creates share mapping+claim safety. Bulk copy passes False after it already
    acquired the durable claim.
    """
    del confirm
    t0 = time.perf_counter()
    dest_ids = [str(x).strip() for x in destination_store_ids if str(x).strip()]
    if not dest_ids:
        raise ValueError("destination_store_ids is required")

    trace = ProductAddTrace.create(workspace_id)
    if correlation_id:
        trace.correlation_id = str(correlation_id)
    token = bind_trace(trace)
    try:
        return _add_product_from_connected_inner(
            workspace_id,
            source_store_id,
            daraz_item_id,
            dest_ids,
            price_override=price_override,
            variant_price_overrides=variant_price_overrides,
            execute=execute,
            allow_duplicates=allow_duplicates,
            edit_before=edit_before,
            resume_by_store=resume_by_store,
            forced_attempt_id=forced_attempt_id,
            forced_seller_skus=forced_seller_skus,
            claim_gate=claim_gate,
            trace=trace,
            t0=t0,
        )
    finally:
        reset_trace(token)


def _add_product_from_connected_inner(
    workspace_id: str,
    source_store_id: str,
    daraz_item_id: str,
    dest_ids: list[str],
    *,
    price_override: float | None,
    variant_price_overrides: dict[str, float] | None,
    execute: bool | None,
    allow_duplicates: bool,
    edit_before: bool,
    resume_by_store: dict[str, str] | None,
    forced_attempt_id: str | None,
    forced_seller_skus: Any,
    claim_gate: bool,
    trace: ProductAddTrace,
    t0: float,
) -> dict[str, Any]:
    gate_on = product_create_enabled()
    if edit_before:
        will_execute = False
    elif not gate_on:
        will_execute = False
    elif execute is False:
        will_execute = False
    else:
        will_execute = True

    trace.stage(
        "gate_checked",
        gate_on=gate_on,
        execute=will_execute,
        edit_before=bool(edit_before),
        dest_count=len(dest_ids),
        source="connected",
    )

    source_store_row = _resolve_dest(workspace_id, source_store_id)
    if not source_store_row:
        raise ValueError("Source store not found")
    # Fail closed before any fetch/create when peer copy_products is missing.
    for dest_ref in dest_ids:
        dest_row = _resolve_dest(workspace_id, dest_ref)
        perm = _enforce_copy_permission(
            workspace_id,
            source_store=source_store_row,
            destination_store=dest_row,
        )
        if not perm.get("allowed"):
            raise PermissionError(
                f"copy_permission_denied:{perm.get('reason') or 'copy_products_not_granted'}"
            )

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
        _apply_price_override(
            draft_result.get("draft") or {},
            price_override,
            variant_price_overrides,
        )
        trace.stage("edit_before_draft", dest_count=len(dest_ids), gate_on=gate_on)
        return {
            "status": "EDIT_BEFORE",
            "source": source_meta,
            "edit_before": True,
            "draft_result": draft_result,
            "destination_store_ids": dest_ids,
            "product_create_enabled": gate_on,
            "correlation_id": trace.correlation_id,
            "stage_history": trace.public_history(),
            "timings_ms": {
                **(fetched.get("timings_ms") or {}),
                "total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    resume_map = {
        str(k): str(v)
        for k, v in (resume_by_store or {}).items()
        if k is not None and v is not None
    }
    defaults, store_map, skus_by_dest = _prefetch_destination_context(
        workspace_id, dest_ids
    )
    _refresh_destination_tokens(workspace_id, list(store_map.values()))
    defaults, store_map, skus_by_dest = _prefetch_destination_context(
        workspace_id, dest_ids
    )

    jobs: list[tuple[int, dict[str, Any]]] = []
    placeholders: list[dict[str, Any] | None] = [None] * len(dest_ids)
    source_store_uuid = str(product["store_id"])

    for idx, dest_ref in enumerate(dest_ids):
        dest = store_map.get(dest_ref)
        if not dest:
            placeholders[idx] = {
                "store": {"store_id": dest_ref, "display_name": dest_ref},
                "status": "NEEDS_ATTENTION",
                "reason": "destination_store_not_found",
            }
            continue
        if str(dest["id"]) == source_store_uuid:
            placeholders[idx] = {
                "store": _store_label(dest),
                "status": "NEEDS_ATTENTION",
                "reason": "destination_must_differ_from_source",
            }
            continue
        jobs.append(
            (
                idx,
                {
                    "dest": dest,
                    "resume_id": resume_map.get(str(dest.get("store_id")))
                    or resume_map.get(str(dest.get("id"))),
                },
            )
        )

    def _one(payload: dict[str, Any]) -> dict[str, Any]:
        dest = payload["dest"]
        try:
            draft_result = build_connected_clone_draft(
                workspace_id,
                source_product_id=product_id,
                destination_store_id=str(dest.get("store_id") or dest["id"]),
                product_defaults=defaults,
                existing_seller_skus=skus_by_dest.get(str(dest["id"])),
                destination_store=dest,
            )
            draft = copy.deepcopy(draft_result.get("draft") or {})
        except ValueError as exc:
            return {
                "store": _store_label(dest),
                "status": "NEEDS_ATTENTION",
                "reason": str(exc),
            }
        return _prepare_destination(
            workspace_id,
            draft=draft,
            dest=dest,
            price_override=price_override,
            variant_price_overrides=variant_price_overrides,
            allow_duplicates=allow_duplicates,
            execute=will_execute,
            confirm=True,
            gate_on=gate_on,
            resume_item_id=payload.get("resume_id"),
            forced_attempt_id=forced_attempt_id,
            forced_seller_skus=forced_seller_skus,
            claim_gate=claim_gate,
        )

    parallelism = min(_destination_concurrency(), max(1, len(jobs))) if jobs else 0
    ran = _run_destinations_bounded(jobs, worker=_one, max_workers=max(1, parallelism))
    destinations: list[dict[str, Any]] = []
    for idx in range(len(dest_ids)):
        if placeholders[idx] is not None:
            destinations.append(placeholders[idx] or {})
        else:
            destinations.append(ran.get(idx) or {"status": "NEEDS_ATTENTION"})

    summary = _summarize(destinations, gate_on=gate_on, execute=will_execute)
    if not gate_on and summary["status"] not in {"NEEDS_ATTENTION", "FAILED"}:
        summary["status"] = "BLOCKED_CREATE"

    for dest_row in destinations:
        if not isinstance(dest_row, dict):
            continue
        trace.stage(
            "destination_outcome",
            store_id=str((dest_row.get("store") or {}).get("store_id") or ""),
            status=str(dest_row.get("status") or ""),
            reason=str(dest_row.get("reason") or "")[:120] or None,
            attempt_id=str(dest_row.get("attempt_id") or "") or None,
        )
    trace.stage(
        "completed",
        status=str(summary.get("status") or ""),
        created_count=int(summary.get("created_count") or 0),
        failed_count=int(summary.get("failed_count") or 0),
        needs_attention_count=int(summary.get("needs_attention_count") or 0),
        elapsed_ms=trace.elapsed_ms(),
    )

    return {
        **summary,
        "source": source_meta,
        "destinations": destinations,
        "correlation_id": trace.correlation_id,
        "stage_history": trace.public_history(),
        "timings_ms": {
            **(fetched.get("timings_ms") or {}),
            "destination_parallelism": parallelism,
            "total_ms": round((time.perf_counter() - t0) * 1000, 1),
            "total": round((time.perf_counter() - t0) * 1000, 1),
        },
    }
