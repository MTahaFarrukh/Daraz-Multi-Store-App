"""Durable verified source→destination product copy identity + create claims.

Primary key (unique): workspace + source_store_uuid + source_daraz_item_id + destination_store_uuid.
Independent of bulk-job fingerprints and regenerated Seller SKUs.

Create claims use a short atomic DB upsert; the lease is not held across Daraz HTTP.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
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

PRODUCT_COPY_CLAIM_LEASE_SECONDS = max(
    60, int(os.environ.get("PRODUCT_COPY_CLAIM_LEASE_SECONDS", "900") or "900")
)


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat()


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
    mapping = repo.upsert_verified_product_copy_mapping(
        {
            "workspace_id": str(workspace_id),
            "source_store_id": str(source_store_id),
            "source_daraz_item_id": src_item,
            "destination_store_id": str(destination_store_id),
            "destination_item_id": dest_item,
            "attempt_id": attempt_id,
            "verified_at": _now_iso(),
        }
    )
    # After verified success, free any claim so the row does not stay uncertain forever.
    claim = repo.get_product_copy_claim(
        workspace_id,
        source_store_id=str(source_store_id),
        source_daraz_item_id=src_item,
        destination_store_id=str(destination_store_id),
    )
    if claim and str(claim.get("state")) in {"held", "uncertain"}:
        token = str(claim.get("owner_token") or "")
        if token:
            repo.update_product_copy_claim(
                workspace_id,
                str(claim["id"]),
                owner_token=token,
                fields={
                    "state": "released",
                    "heartbeat_at": _now_iso(),
                    "lease_expires_at": _now_iso(),
                    "attempt_id": attempt_id or claim.get("attempt_id"),
                },
            )
    return mapping


def acquire_create_claim(
    workspace_id: str,
    *,
    source_store_id: str,
    source_daraz_item_id: str,
    destination_store_id: str,
    owner_job_id: str | None = None,
    owner_item_id: str | None = None,
    lease_seconds: int | None = None,
) -> dict[str, Any]:
    """Try to acquire the exclusive create claim.

    Returns:
      {"acquired": True, "claim": ..., "owner_token": ...}
      {"acquired": False, "claim": existing_or_none, "reason": "..."}
    """
    repo = get_repo()
    # Re-check verified mapping under caller's flow; claim only for create path.
    existing_claim = repo.get_product_copy_claim(
        workspace_id,
        source_store_id=str(source_store_id),
        source_daraz_item_id=str(source_daraz_item_id),
        destination_store_id=str(destination_store_id),
    )
    if existing_claim and str(existing_claim.get("state")) == "uncertain":
        return {
            "acquired": False,
            "claim": existing_claim,
            "reason": "create_claim_uncertain_requires_reconciliation",
        }

    owner_token = str(uuid.uuid4())
    lease = int(lease_seconds if lease_seconds is not None else PRODUCT_COPY_CLAIM_LEASE_SECONDS)
    lease_expires = (_now() + timedelta(seconds=lease)).isoformat()
    claim = repo.try_acquire_product_copy_claim(
        {
            "workspace_id": str(workspace_id),
            "source_store_id": str(source_store_id),
            "source_daraz_item_id": str(source_daraz_item_id),
            "destination_store_id": str(destination_store_id),
            "owner_token": owner_token,
            "owner_job_id": owner_job_id,
            "owner_item_id": owner_item_id,
            "lease_expires_at": lease_expires,
        }
    )
    if claim and str(claim.get("owner_token")) == owner_token:
        return {"acquired": True, "claim": claim, "owner_token": owner_token, "reason": None}

    # Lost race or live foreign hold
    held = claim or existing_claim or repo.get_product_copy_claim(
        workspace_id,
        source_store_id=str(source_store_id),
        source_daraz_item_id=str(source_daraz_item_id),
        destination_store_id=str(destination_store_id),
    )
    reason = "concurrent_create_claim_held"
    if held and str(held.get("state")) == "uncertain":
        reason = "create_claim_uncertain_requires_reconciliation"
    return {"acquired": False, "claim": held, "owner_token": None, "reason": reason}


def mark_create_claim_uncertain(
    workspace_id: str,
    *,
    claim_id: str,
    owner_token: str,
    attempt_id: str | None = None,
) -> dict[str, Any] | None:
    """Keep exclusive ownership but block other creates until reconcile succeeds."""
    repo = get_repo()
    # Far-future lease so stale reclaim cannot steal during uncertain period.
    far = (_now() + timedelta(days=3650)).isoformat()
    return repo.update_product_copy_claim(
        workspace_id,
        claim_id,
        owner_token=owner_token,
        fields={
            "state": "uncertain",
            "attempt_id": attempt_id,
            "heartbeat_at": _now_iso(),
            "lease_expires_at": far,
        },
    )


def release_create_claim(
    workspace_id: str,
    *,
    claim_id: str,
    owner_token: str,
) -> dict[str, Any] | None:
    """Release claim after verified mapping or safe-to-retry failure."""
    repo = get_repo()
    return repo.update_product_copy_claim(
        workspace_id,
        claim_id,
        owner_token=owner_token,
        fields={
            "state": "released",
            "heartbeat_at": _now_iso(),
            "lease_expires_at": _now_iso(),
        },
    )


def evaluate_stale_claim_before_create(
    workspace_id: str,
    *,
    source_store_id: str,
    source_daraz_item_id: str,
    destination_store_id: str,
) -> dict[str, Any]:
    """After acquiring a possibly-stale claim, decide if CreateProduct is safe.

    Returns action: proceed | skip_verified | needs_reconciliation
    """
    mapping = get_verified_copy_mapping(
        workspace_id,
        source_store_id=source_store_id,
        source_daraz_item_id=source_daraz_item_id,
        destination_store_id=destination_store_id,
    )
    if mapping:
        return {"action": "skip_verified", "mapping": mapping, "attempt": None}
    uncertain = find_uncertain_create_attempt(
        workspace_id,
        source_store_id=source_store_id,
        source_daraz_item_id=source_daraz_item_id,
        destination_store_id=destination_store_id,
    )
    if uncertain:
        return {"action": "needs_reconciliation", "mapping": None, "attempt": uncertain}
    return {"action": "proceed", "mapping": None, "attempt": None}


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
