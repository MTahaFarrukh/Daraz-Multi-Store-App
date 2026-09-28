"""Thin audit helpers for route-layer mutation events (safe metadata only)."""

from __future__ import annotations

from typing import Any

from src.audit_log import audit_event
from src.db.repo import get_repo


def audit_mutation(
    *,
    workspace_id: str,
    actor_user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        audit_event(
            get_repo(),
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata=metadata or {},
        )
    except Exception:  # noqa: BLE001
        # Audit must never break the primary mutation path.
        return


def audit_product_create_result(
    *,
    workspace_id: str,
    actor_user_id: str | None,
    result: dict[str, Any],
    source: str,
) -> None:
    audit_mutation(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action="product.create.requested",
        entity_type="product_add",
        entity_id=None,
        metadata={
            "source": source,
            "status": result.get("status"),
            "destination_count": len(result.get("destinations") or []),
        },
    )
    for dest in result.get("destinations") or []:
        if not isinstance(dest, dict):
            continue
        store = dest.get("store") or {}
        attempt_id = dest.get("attempt_id")
        creation = str(dest.get("creation_status") or dest.get("status") or "")
        meta = {
            "destination_store_id": store.get("store_id") or store.get("id"),
            "attempt_id": attempt_id,
            "status": creation,
            "item_id": dest.get("item_id"),
        }
        if creation in {"CREATED", "VERIFIED"} or dest.get("status") in {
            "Created",
            "Active",
            "Pending QC",
        }:
            action = "product.create.verified"
        elif creation in {"FAILED_SAFE_TO_RETRY", "FAILED", "NEEDS_RECONCILIATION"} or dest.get(
            "status"
        ) in {"Failed", "FAILED"}:
            action = "product.create.failed"
            meta["error_code"] = (dest.get("reason") or "")[:120]
        else:
            continue
        audit_mutation(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type="product_create_attempt",
            entity_id=str(attempt_id) if attempt_id else None,
            metadata=meta,
        )
