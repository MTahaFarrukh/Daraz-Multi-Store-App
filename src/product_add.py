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
) -> dict[str, Any]:
    """Validate one destination draft; optionally CreateProduct when gated+confirmed."""
    del confirm  # probe-only concept; kept for call-site compatibility
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    store_info = _store_label(dest)
    override_report = _apply_price_override(
        draft, price_override, variant_price_overrides
    )

    unresolved = list_unresolved_price_variants(draft)
    if unresolved:
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
    duplicates = list(draft.get("possible_duplicates") or [])
    if not duplicates:
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
    dup_kind, dup_best = classify_duplicate_matches(duplicates)
    timings["duplicate_check_ms"] = round((time.perf_counter() - t_dup) * 1000, 1)

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
            cat_payload = client.get_category_attributes(primary)
            timings["category_resolution_ms"] = round(
                (time.perf_counter() - t_cat) * 1000, 1
            )
            t_brand = time.perf_counter()
            brand_resolution = resolve_brand_for_category(
                source_brand=(draft.get("product") or {}).get("brand"),
                primary_category_id=primary,
                query_brands=client.query_category_brands,
            )
            timings["brand_resolution_ms"] = round(
                (time.perf_counter() - t_brand) * 1000, 1
            )
            used_no_brand = bool(brand_resolution.get("used_no_brand")) or (
                brand_resolution.get("status") == "NO_BRAND"
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
            timings.setdefault(
                "category_resolution_ms",
                round((time.perf_counter() - t_cat) * 1000, 1),
            )
    else:
        errors.append("category:PrimaryCategory unresolved")

    media = draft.get("media") or {}
    source_images = list(
        media.get("product_images") or media.get("resolved_images") or []
    )
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
        and brand_resolution.get("status") in {"EXACT_MATCH", "NO_BRAND"}
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

    t_create = time.perf_counter()
    xml = build_create_product_xml(draft)
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
        return {
            **result_base,
            "status": "Failed",
            "creation_status": "FAILED",
            "reason": f"CreateProduct:{exc.code}:{exc}{suffix}",
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
                "dest_total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }

    timings["create_product_ms"] = round((time.perf_counter() - t_create) * 1000, 1)
    item_id = _extract_item_id_from_create(resp)

    t_sp = time.perf_counter()
    special = _apply_special_prices(client, draft)
    timings["special_price_ms"] = float(
        special.get("timings_ms") or round((time.perf_counter() - t_sp) * 1000, 1)
    )

    t_ver = time.perf_counter()
    post = _post_create_status(client, item_id)
    fidelity = verify_pricing_fidelity(draft, post.get("raw"))
    timings["verification_ms"] = round((time.perf_counter() - t_ver) * 1000, 1)

    catalog_upsert: dict[str, Any] = {"ok": False}
    if item_id:
        catalog_upsert = _local_catalog_upsert(
            workspace_id,
            dest,
            item_id=str(item_id),
            draft=draft,
            live_item=post.get("raw"),
        )

    warnings: list[str] = []
    status = post["status"]
    creation_status = "CREATED"
    # Only treat hard pricing mismatch as warning — missing live SKUs is PARTIAL.
    if (
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

    special_detected = sum(
        1
        for v in (draft.get("variants") or [])
        if isinstance(v, dict) and parse_money(v.get("special_price")) is not None
    )

    return {
        **result_base,
        "status": status,
        "creation_status": creation_status,
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
            "dest_total": round((time.perf_counter() - t0) * 1000, 1),
        },
    }


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


def _merge_timings(*parts: dict[str, Any] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        for k, v in part.items():
            try:
                out[str(k)] = float(v)
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

    gate_on = product_create_enabled()
    if edit_before:
        will_execute = False
    elif not gate_on:
        will_execute = False
    elif execute is False:
        will_execute = False
    else:
        will_execute = True

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
        return {
            "status": "EDIT_BEFORE",
            "source": source_meta,
            "edit_before": True,
            "draft_result": draft_result,
            "destination_store_ids": dest_ids,
            "product_create_enabled": gate_on,
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

        resume_id = resume_map.get(str(dest.get("store_id"))) or resume_map.get(
            str(dest.get("id"))
        )
        destinations.append(
            _prepare_destination(
                workspace_id,
                draft=draft,
                dest=dest,
                price_override=price_override,
                variant_price_overrides=variant_price_overrides,
                allow_duplicates=allow_duplicates,
                execute=will_execute,
                confirm=True,
                gate_on=gate_on,
                resume_item_id=resume_id,
            )
        )

    summary = _summarize(destinations, gate_on=gate_on, execute=will_execute)
    if not gate_on and summary["status"] not in {"NEEDS_ATTENTION", "FAILED"}:
        summary["status"] = "BLOCKED_CREATE"

    return {
        **summary,
        "source": source_meta,
        "destinations": destinations,
        "timings_ms": _merge_timings(
            extract_timings,
            {
                "fetch_extract": fetch_ms,
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
) -> dict[str, Any]:
    """Fetch connected item once → prepare / create on each destination."""
    del confirm
    t0 = time.perf_counter()
    dest_ids = [str(x).strip() for x in destination_store_ids if str(x).strip()]
    if not dest_ids:
        raise ValueError("destination_store_ids is required")

    gate_on = product_create_enabled()
    if edit_before:
        will_execute = False
    elif not gate_on:
        will_execute = False
    elif execute is False:
        will_execute = False
    else:
        will_execute = True

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

    resume_map = {
        str(k): str(v)
        for k, v in (resume_by_store or {}).items()
        if k is not None and v is not None
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

        resume_id = resume_map.get(str(dest.get("store_id"))) or resume_map.get(
            str(dest.get("id"))
        )
        destinations.append(
            _prepare_destination(
                workspace_id,
                draft=draft,
                dest=dest,
                price_override=price_override,
                variant_price_overrides=variant_price_overrides,
                allow_duplicates=allow_duplicates,
                execute=will_execute,
                confirm=True,
                gate_on=gate_on,
                resume_item_id=resume_id,
            )
        )

    summary = _summarize(destinations, gate_on=gate_on, execute=will_execute)
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
