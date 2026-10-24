"""Durable verified source→destination product copy identity.

Primary key (unique): workspace + source_store_uuid + source_daraz_item_id + destination_store_uuid.
Independent of bulk-job fingerprints and regenerated Seller SKUs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.db.repo import get_repo

_UNCERTAIN_ATTEMPT_STATES = frozenset(
    {
        "CREATING",
        "CREATED_UNVERIFIED",
        "NEEDS_RECONCILIATION",
    }
)
_VERIFIED_ATTEMPT_STATES = frozenset({"VERIFIED"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def connected_source_identities(
    *,
    source_store_slug: str | None,
    source_store_uuid: str | None,
    source_daraz_item_id: str,
) -> list[str]:
    """Identity strings historically stored on product_create_attempts."""
    item = str(source_daraz_item_id or "").strip()
    if not item:
        return []
    out: list[str] = []
    for store_ref in (source_store_slug, source_store_uuid):
        ref = str(store_ref or "").strip()
        if not ref:
            continue
        identity = f"connected:{ref}:{item}"
        if identity not in out:
            out.append(identity)
    return out


def get_verified_copy_mapping(
    workspace_id: str,
    *,
    source_store_id: str,
    source_daraz_item_id: str,
    destination_store_id: str,
) -> dict[str, Any] | None:
    repo = get_repo()
    mapping = repo.get_verified_product_copy_mapping(
        workspace_id,
        source_store_id=str(source_store_id),
        source_daraz_item_id=str(source_daraz_item_id),
        destination_store_id=str(destination_store_id),
    )
    if mapping:
        return mapping
    # Conservative legacy backfill from verified create attempts only.
    return backfill_mapping_from_verified_attempts(
        workspace_id,
        source_store_id=str(source_store_id),
        source_daraz_item_id=str(source_daraz_item_id),
        destination_store_id=str(destination_store_id),
    )


def backfill_mapping_from_verified_attempts(
    workspace_id: str,
    *,
    source_store_id: str,
    source_daraz_item_id: str,
    destination_store_id: str,
) -> dict[str, Any] | None:
    repo = get_repo()
    source = repo.get_store_by_uuid(workspace_id, source_store_id)
    identities = connected_source_identities(
        source_store_slug=(source or {}).get("store_id"),
        source_store_uuid=source_store_id,
        source_daraz_item_id=source_daraz_item_id,
    )
    if not identities:
        return None
    attempts = repo.find_product_attempts_for_source_dest(
        workspace_id,
        source_identities=identities,
        destination_store_id=str(destination_store_id),
        states=list(_VERIFIED_ATTEMPT_STATES),
    )
    for attempt in attempts:
        dest_item = str(attempt.get("destination_item_id") or "").strip()
        if not dest_item:
            continue
        return record_verified_copy_mapping(
            workspace_id,
            source_store_id=str(source_store_id),
            source_daraz_item_id=str(source_daraz_item_id),
            destination_store_id=str(destination_store_id),
            destination_item_id=dest_item,
            attempt_id=str(attempt.get("id") or "") or None,
        )
    return None


def find_uncertain_create_attempt(
    workspace_id: str,
    *,
    source_store_id: str,
    source_daraz_item_id: str,
    destination_store_id: str,
) -> dict[str, Any] | None:
    """Return an open/uncertain attempt that must be reconciled before another create."""
    repo = get_repo()
    source = repo.get_store_by_uuid(workspace_id, source_store_id)
    identities = connected_source_identities(
        source_store_slug=(source or {}).get("store_id"),
        source_store_uuid=source_store_id,
        source_daraz_item_id=source_daraz_item_id,
    )
    if not identities:
        return None
    attempts = repo.find_product_attempts_for_source_dest(
        workspace_id,
        source_identities=identities,
        destination_store_id=str(destination_store_id),
        states=list(_UNCERTAIN_ATTEMPT_STATES),
    )
    return attempts[0] if attempts else None


def record_verified_copy_mapping(
    workspace_id: str,
    *,
    source_store_id: str,
    source_daraz_item_id: str,
    destination_store_id: str,
    destination_item_id: str,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """Persist mapping only after positive verification with a known destination item id."""
    dest_item = str(destination_item_id or "").strip()
    if not dest_item:
        raise ValueError("destination_item_id required for verified copy mapping")
    src_item = str(source_daraz_item_id or "").strip()
    if not src_item:
        raise ValueError("source_daraz_item_id required for verified copy mapping")
    repo = get_repo()
    return repo.upsert_verified_product_copy_mapping(
        {
            "workspace_id": str(workspace_id),
            "source_store_id": str(source_store_id),
            "source_daraz_item_id": src_item,
            "destination_store_id": str(destination_store_id),
            "destination_item_id": dest_item,
            "attempt_id": attempt_id,
            "verified_at": _now(),
        }
    )


def try_record_verified_from_attempt(
    workspace_id: str,
    attempt: dict[str, Any],
    *,
    source_store_id: str | None = None,
    source_daraz_item_id: str | None = None,
) -> dict[str, Any] | None:
    """Record mapping from a VERIFIED attempt when identity fields are reliable."""
    if str(attempt.get("state") or "") != "VERIFIED":
        return None
    dest_item = str(attempt.get("destination_item_id") or "").strip()
    if not dest_item:
        return None
    dest_store = str(attempt.get("destination_store_id") or "").strip()
    if not dest_store:
        return None

    src_store = str(source_store_id or "").strip()
    src_item = str(source_daraz_item_id or "").strip()
    if not src_store or not src_item:
        identity = str(attempt.get("source_identity") or "")
        # connected:{store_ref}:{item}
        if identity.startswith("connected:"):
            parts = identity.split(":", 2)
            if len(parts) == 3:
                store_ref, item = parts[1], parts[2]
                src_item = src_item or item
                if not src_store:
                    repo = get_repo()
                    by_slug = repo.get_store(workspace_id, store_ref)
                    by_uuid = repo.get_store_by_uuid(workspace_id, store_ref)
                    store = by_slug or by_uuid
                    if store:
                        src_store = str(store["id"])
    if not src_store or not src_item:
        return None
    try:
        return record_verified_copy_mapping(
            workspace_id,
            source_store_id=src_store,
            source_daraz_item_id=src_item,
            destination_store_id=dest_store,
            destination_item_id=dest_item,
            attempt_id=str(attempt.get("id") or "") or None,
        )
    except ValueError:
        return None
