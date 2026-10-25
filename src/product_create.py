"""Supervised CreateProduct path — explicit, env-gated, never auto-run.

Probe authorization (ALLOW_PRODUCT_CREATE_PROBE) is isolated from ordinary
product creation (ALLOW_PRODUCT_CREATE). Enabling the probe must not unlock
Add Product, URL add, connected multi-dest create, or bulk copy.
"""

from __future__ import annotations

import os
from typing import Any

from src.db.repo import get_repo
from src.product_clone import build_connected_clone_draft
from src.product_create_payload import build_create_product_payload_preview


def create_probe_enabled() -> bool:
    """Supervised create-probe endpoint only — does NOT authorize ordinary creates."""
    return os.getenv("ALLOW_PRODUCT_CREATE_PROBE", "").lower() in {"1", "true", "yes"}


def product_create_enabled() -> bool:
    """True only when ALLOW_PRODUCT_CREATE authorizes ordinary create paths.

    Probe flag is intentionally excluded so supervised sessions cannot unlock
    Add Product / URL / bulk creation by enabling PROBE alone.
    """
    return os.getenv("ALLOW_PRODUCT_CREATE", "").lower() in {"1", "true", "yes"}


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
    - ALLOW_PRODUCT_CREATE_PROBE enabled (probe-only; not ALLOW_PRODUCT_CREATE)
    - confirm=True
    - execute=True to call Daraz CreateProduct (otherwise mutation-free dry-run)
    - Same durable claim / mapping / uncertain-attempt gates as single-dest add
    - destination ≠ source
    - category + brand validation pass
    - no strong duplicates unless allow_duplicates=True (operator override)

    Dry-run (execute=false) never calls CreateProduct, UpdatePrice, or image migrate.
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

    source_product = repo.get_daraz_product(workspace_id, source_product_id)
    if not source_product:
        return {"status": "BLOCKED", "reason": "Source product not found"}

    source_store = repo.get_store_by_uuid(
        workspace_id, str(source_product.get("store_id") or "")
    )
    from src.workspace_connections import assert_product_copy_allowed

    perm = assert_product_copy_allowed(
        actor_workspace_id=workspace_id,
        source_store=source_store,
        destination_store=dest,
        source_workspace_id=(
            str(source_store.get("workspace_id"))
            if source_store and source_store.get("workspace_id")
            else None
        ),
    )
    if not perm.get("allowed"):
        return {
            "status": "BLOCKED",
            "reason": str(perm.get("reason") or "copy_permission_denied"),
            "code": str(perm.get("code") or "copy_permission_denied"),
            "copy_permission": perm,
        }

    if source_store and str(source_store.get("id")) == str(dest.get("id")):
        return {
            "status": "BLOCKED",
            "reason": "destination_must_differ_from_source",
        }

    # Soft token refresh — read-only for dry-run; create path still needs a client.
    try:
        from src.token_refresh import refresh_store_tokens

        refresh_store_tokens(
            store_ids=[str(dest.get("store_id") or dest.get("id"))],
            within_minutes=30,
            workspace_id=workspace_id,
        )
        dest = (
            repo.get_store(workspace_id, destination_store_id)
            or repo.get_store_by_uuid(workspace_id, destination_store_id)
            or dest
        )
    except Exception:  # noqa: BLE001
        pass

    try:
        base = build_connected_clone_draft(
            workspace_id,
            source_product_id=source_product_id,
            destination_store_id=destination_store_id,
            destination_store=dest,
        )
    except ValueError as exc:
        return {"status": "BLOCKED", "reason": str(exc)}

    draft = base.get("draft") or {}

    # Reuse the same destination pipeline as Add Product (claims, mapping, attempts).
    # gate_on=True authorizes this supervised session only — ordinary paths still
    # require product_create_enabled() / ALLOW_PRODUCT_CREATE.
    from src.product_add import _prepare_destination

    dest_out = _prepare_destination(
        workspace_id,
        draft=draft,
        dest=dest,
        price_override=None,
        allow_duplicates=allow_duplicates,
        execute=bool(execute),
        confirm=True,
        gate_on=True,
        claim_gate=True,
        allow_image_migrate=bool(execute),
    )

    return _map_supervised_result(
        dest_out,
        execute=bool(execute),
        source_product_id=source_product_id,
        destination_store_id=str(dest.get("store_id") or destination_store_id),
        draft=draft,
    )


def _map_supervised_result(
    dest_out: dict[str, Any],
    *,
    execute: bool,
    source_product_id: str,
    destination_store_id: str,
    draft: dict[str, Any],
) -> dict[str, Any]:
    """Normalize single-dest add outcomes into supervised create-probe shapes."""
    status = str(dest_out.get("status") or "")
    creation = str(dest_out.get("creation_status") or "")
    reason = str(dest_out.get("reason") or "")
    preview = dest_out.get("draft_preview") or build_create_product_payload_preview(
        draft
    )
    brand = dest_out.get("brand_resolution") or draft.get("brand_resolution")
    category = dest_out.get("category") or (draft.get("validation") or {}).get(
        "category"
    )
    base: dict[str, Any] = {
        "preview": preview,
        "validation": dest_out.get("validation") or draft.get("validation"),
        "brand_resolution": brand,
        "category": category,
        "mtf_skus": [
            v.get("seller_sku")
            for v in (draft.get("variants") or [])
            if isinstance(v, dict)
        ],
        "image_strategy": (draft.get("media") or {}).get("image_strategy")
        or ((preview or {}).get("image_strategy") if isinstance(preview, dict) else None),
        "source_product_id": source_product_id,
        "destination_store_id": destination_store_id,
        "variant_count": dest_out.get("variant_count")
        or len([v for v in (draft.get("variants") or []) if isinstance(v, dict)]),
        "attempt_id": dest_out.get("attempt_id"),
        "item_id": dest_out.get("item_id") or dest_out.get("existing_daraz_item_id"),
        "creation_status": creation or None,
        "copy_permission": "same_workspace_or_granted",
        "store": dest_out.get("store"),
        "timings_ms": dest_out.get("timings_ms"),
    }

    # Mapping / claim / uncertain blocks
    if status in {"ALREADY_EXISTS"} or reason in {
        "already_copied_to_destination",
    }:
        return {
            **base,
            "status": "NOT_RUN",
            "reason": reason or "already_copied_to_destination",
        }

    if reason in {
        "product_create_attempt_requires_reconciliation",
        "stale_claim_requires_reconciliation",
        "create_claim_uncertain_requires_reconciliation",
        "concurrent_create_claim_held",
    } or creation in {"NEEDS_RECONCILIATION"}:
        return {
            **base,
            "status": "NOT_RUN",
            "reason": reason or "requires_reconciliation",
        }

    if (
        "duplicate" in reason.lower()
        or "already exists" in reason.lower()
        or dest_out.get("duplicates")
    ):
        return {
            **base,
            "status": "NOT_RUN",
            "reason": reason
            or (
                "Possible duplicate(s) on destination — refusing create. "
                "Pick a safe source/destination or pass allow_duplicates "
                "only with explicit operator intent."
            ),
            "duplicates": dest_out.get("duplicates")
            or draft.get("possible_duplicates"),
        }

    if status == "READY" and not execute:
        return {
            **base,
            "status": "READY",
            "reason": (
                "Validation passed; execute=false so CreateProduct was not called "
                "(no image migrate / UpdatePrice)"
            ),
            "payload_xml_length": None,
        }

    # Successful create outcomes from product_add
    if status in {
        "Created",
        "CREATED",
        "CREATED_WITH_WARNING",
        "VERIFIED",
    } or creation in {
        "CREATED",
        "VERIFIED",
        "CREATED_WITH_WARNING",
        "CREATED_UNVERIFIED",
    }:
        out_status = "PROVEN" if base.get("item_id") else "NEEDS_ATTENTION"
        if creation == "CREATED_UNVERIFIED" or not base.get("item_id"):
            out_status = "NEEDS_ATTENTION"
        return {
            **base,
            "status": out_status,
            "reason": reason
            or (
                "create_succeeded"
                if out_status == "PROVEN"
                else "create_outcome_requires_reconciliation"
            ),
            "daraz_response": dest_out.get("daraz_response"),
            "warnings": dest_out.get("warnings"),
        }

    if "CreateProduct" in reason or creation.startswith("FAILED") or status in {
        "Failed",
        "FAILED",
    }:
        return {
            **base,
            "status": "FAILED",
            "reason": reason or status,
            "daraz_error": dest_out.get("daraz_error"),
        }

    if status in {"NEEDS_ATTENTION"} or not execute:
        return {
            **base,
            "status": "NOT_RUN",
            "reason": reason or "Validation failed — CreateProduct not submitted",
        }

    return {
        **base,
        "status": "NOT_RUN",
        "reason": reason or status or "unknown_supervised_outcome",
        "daraz_error": dest_out.get("daraz_error"),
    }
