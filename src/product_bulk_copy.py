"""One-click bulk store-to-store product copy.

Reuses add_product_from_connected + product_create_attempts for per-item safety.
Parent job persists progress so the UI can survive refresh.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from src.db.repo import get_repo
from src.product_add import add_product_from_connected, product_create_enabled
from src.product_copy_mapping import (
    acquire_create_claim,
    evaluate_stale_claim_before_create,
    find_uncertain_create_attempt,
    get_verified_copy_mapping,
    mark_create_claim_uncertain,
    record_verified_copy_mapping,
    release_create_claim,
)
from src.product_sync import sync_store_products

logger = logging.getLogger(__name__)

PRODUCT_BULK_COPY_CONCURRENCY = max(
    1, int(os.environ.get("PRODUCT_BULK_COPY_CONCURRENCY", "2") or "2")
)

_STATUS_LABELS = {
    "queued": "Queued",
    "preparing": "Preparing",
    "creating": "Creating",
    "verifying": "Verifying",
    "verified": "Verified",
    "needs_attention": "Needs Attention",
    "needs_reconciliation": "Needs Reconciliation",
    "failed": "Failed",
    "skipped": "Skipped",
}

_ACTIVE_JOB_STATUSES = frozenset({"queued", "running"})
_TERMINAL_ITEM_STATUSES = frozenset(
    {
        "verified",
        "failed",
        "needs_attention",
        "needs_reconciliation",
        "skipped",
    }
)
_workers_lock = threading.Lock()
_running_workers: set[str] = set()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid() -> str:
    import uuid

    return str(uuid.uuid4())


def human_item_status(status: str | None) -> str:
    key = str(status or "").strip().lower()
    return _STATUS_LABELS.get(key, (status or "Unknown").replace("_", " ").title())


def _fingerprint(
    workspace_id: str,
    source_uuid: str,
    dest_uuid: str,
    item_ids: list[str],
) -> str:
    blob = "|".join(
        [workspace_id, source_uuid, dest_uuid, *sorted(str(i) for i in item_ids)]
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _resolve_store(workspace_id: str, store_ref: str) -> dict[str, Any] | None:
    repo = get_repo()
    return repo.get_store(workspace_id, store_ref) or repo.get_store_by_uuid(
        workspace_id, store_ref
    )


def discover_source_catalog(
    workspace_id: str,
    source_store_id: str,
    *,
    refresh: bool = True,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Discover eligible source products with pagination completeness metadata."""
    repo = get_repo()
    source = _resolve_store(workspace_id, source_store_id)
    if not source:
        raise ValueError("Source store not found")

    catalog_incomplete = False
    catalog_warning: str | None = None
    sync_result: dict[str, Any] | None = None

    if refresh:
        sync_result = sync_store_products(
            workspace_id, source, fetch_details=True, max_products=None
        )
        if sync_result.get("sync_status") == "error":
            catalog_incomplete = True
            catalog_warning = str(
                sync_result.get("sync_error") or "source_catalog_sync_failed"
            )
        elif sync_result.get("warning"):
            catalog_incomplete = True
            catalog_warning = str(sync_result.get("warning"))

    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))

    listed = repo.list_daraz_products(
        workspace_id,
        {
            "store_uuids": [str(source["id"])],
            "page": page,
            "page_size": page_size,
            "sort": "title_asc",
        },
    )
    items = listed.get("items") or []
    total = int(listed.get("total") or len(items))

    # Count ineligible (missing item id or zero variants) across a capped walk.
    ineligible = 0
    eligible_ids: list[str] = []
    walk_page = 1
    seen = 0
    while seen < total and walk_page <= 100:
        batch = repo.list_daraz_products(
            workspace_id,
            {
                "store_uuids": [str(source["id"])],
                "page": walk_page,
                "page_size": 200,
                "sort": "title_asc",
            },
        )
        rows = batch.get("items") or []
        if not rows:
            break
        for p in rows:
            seen += 1
            iid = str(p.get("daraz_item_id") or "")
            vc = int(p.get("variants_count") or 0)
            if not iid:
                ineligible += 1
                continue
            if vc <= 0 and not p.get("detail_complete"):
                # Still include — create path will fetch detail / fail clearly.
                pass
            eligible_ids.append(iid)
        if len(rows) < 200:
            break
        walk_page += 1

    if seen < total:
        catalog_incomplete = True
        catalog_warning = catalog_warning or "catalog_enumeration_truncated"

    preview = []
    for p in items:
        variants = repo.list_daraz_product_variants(workspace_id, str(p["id"]))
        thumb = None
        imgs = p.get("images_json") or []
        if isinstance(imgs, list) and imgs:
            first = imgs[0]
            thumb = first.get("url") if isinstance(first, dict) else (
                first if isinstance(first, str) else None
            )
        preview.append(
            {
                "product_id": p.get("id"),
                "daraz_item_id": p.get("daraz_item_id"),
                "title": p.get("title_en") or p.get("title"),
                "thumbnail_url": thumb,
                "variants_count": len(variants) or int(p.get("variants_count") or 0),
                "detail_complete": bool(p.get("detail_complete")),
                "status": p.get("status_raw"),
                "eligible": bool(p.get("daraz_item_id")),
            }
        )

    return {
        "source_store": {
            "id": source.get("id"),
            "store_id": source.get("store_id"),
            "display_name": source.get("display_name")
            or source.get("store_name")
            or source.get("store_id"),
        },
        "total": total,
        "eligible_count": len(eligible_ids),
        "ineligible_count": ineligible,
        "eligible_item_ids": eligible_ids,
        "catalog_incomplete": catalog_incomplete,
        "catalog_warning": catalog_warning,
        "sync": sync_result,
        "page": page,
        "page_size": page_size,
        "items": preview,
    }


def _map_dest_status(dest_result: dict[str, Any]) -> tuple[str, str | None, str | None, str | None]:
    raw = str(
        dest_result.get("attempt_state")
        or dest_result.get("creation_status")
        or dest_result.get("status")
        or ""
    ).upper()
    attempt_id = dest_result.get("attempt_id")
    item_id = dest_result.get("item_id") or dest_result.get("existing_daraz_item_id")
    reason = dest_result.get("reason")

    if raw in {"VERIFIED", "ALREADY_EXISTS", "CREATED"}:
        return "verified", attempt_id, item_id, reason
    if raw in {"NEEDS_RECONCILIATION", "CREATED_UNVERIFIED"}:
        return "needs_reconciliation", attempt_id, item_id, reason
    if raw in {"FAILED_SAFE_TO_RETRY", "FAILED"}:
        return "failed", attempt_id, item_id, reason
    if raw in {"NEEDS_ATTENTION", "POSSIBLE_DUPLICATE", "CREATED_WITH_WARNING"}:
        return "needs_attention", attempt_id, item_id, reason
    if raw in {"BLOCKED_CREATE", "READY"}:
        return "needs_attention", attempt_id, item_id, reason or "create_blocked_or_not_executed"
    return "needs_attention", attempt_id, item_id, reason or raw or "unknown_outcome"


def _recompute_job_counts(repo: Any, job: dict[str, Any]) -> dict[str, Any]:
    items = repo.list_product_copy_job_items(
        str(job["workspace_id"]), str(job["id"]), page=1, page_size=10_000
    )
    rows = items.get("items") or []
    verified = sum(1 for r in rows if r.get("status") == "verified")
    failed = sum(1 for r in rows if r.get("status") == "failed")
    attn = sum(1 for r in rows if r.get("status") == "needs_attention")
    recon = sum(1 for r in rows if r.get("status") == "needs_reconciliation")
    skipped = sum(1 for r in rows if r.get("status") == "skipped")
    done = sum(
        1
        for r in rows
        if r.get("status")
        in {
            "verified",
            "failed",
            "needs_attention",
            "needs_reconciliation",
            "skipped",
        }
    )
    patch = {
        "verified_count": verified,
        "failed_count": failed,
        "needs_attention_count": attn,
        "needs_reconciliation_count": recon,
        "skipped_count": skipped,
        "completed_items": done,
        "updated_at": _now(),
    }
    return repo.update_product_copy_job(str(job["workspace_id"]), str(job["id"]), patch)


def _process_one_item(
    workspace_id: str,
    job: dict[str, Any],
    item: dict[str, Any],
    dest_slug: str,
) -> None:
    repo = get_repo()
    item_id = str(item["id"])
    daraz_item = str(item.get("source_daraz_item_id") or "")
    source_uuid = str(job.get("source_store_id") or "")
    dest_uuid = str(job.get("destination_store_id") or "")
    claim_id: str | None = None
    owner_token: str | None = None
    try:
        # 1) Durable verified mapping — skip before any CreateProduct path.
        mapping = get_verified_copy_mapping(
            workspace_id,
            source_store_id=source_uuid,
            source_daraz_item_id=daraz_item,
            destination_store_id=dest_uuid,
        )
        if mapping:
            repo.update_product_copy_job_item(
                workspace_id,
                item_id,
                {
                    "status": "skipped",
                    "attempt_id": mapping.get("attempt_id"),
                    "destination_item_id": mapping.get("destination_item_id"),
                    "reason": "already_copied_to_destination",
                    "updated_at": _now(),
                },
            )
            return

        # 2) Uncertain prior attempt for same source→dest — reconcile, do not recreate.
        uncertain = find_uncertain_create_attempt(
            workspace_id,
            source_store_id=source_uuid,
            source_daraz_item_id=daraz_item,
            destination_store_id=dest_uuid,
        )
        if uncertain:
            repo.update_product_copy_job_item(
                workspace_id,
                item_id,
                {
                    "status": "needs_reconciliation",
                    "attempt_id": uncertain.get("id"),
                    "destination_item_id": uncertain.get("destination_item_id"),
                    "reason": "product_create_attempt_requires_reconciliation",
                    "updated_at": _now(),
                },
            )
            return

        # 3) Atomic durable claim — short DB op, not held across Daraz HTTP.
        acquired = acquire_create_claim(
            workspace_id,
            source_store_id=source_uuid,
            source_daraz_item_id=daraz_item,
            destination_store_id=dest_uuid,
            owner_job_id=str(job.get("id") or "") or None,
            owner_item_id=item_id,
        )
        if not acquired.get("acquired"):
            held = acquired.get("claim") or {}
            reason = str(acquired.get("reason") or "concurrent_create_claim_held")
            if reason == "create_claim_uncertain_requires_reconciliation":
                status = "needs_reconciliation"
            else:
                status = "needs_attention"
            repo.update_product_copy_job_item(
                workspace_id,
                item_id,
                {
                    "status": status,
                    "attempt_id": held.get("attempt_id"),
                    "destination_item_id": None,
                    "reason": reason,
                    "updated_at": _now(),
                },
            )
            return

        claim = acquired["claim"]
        claim_id = str(claim["id"])
        owner_token = str(acquired["owner_token"])

        # 4) After (possibly stale) reclaim, re-evaluate before CreateProduct.
        gate = evaluate_stale_claim_before_create(
            workspace_id,
            source_store_id=source_uuid,
            source_daraz_item_id=daraz_item,
            destination_store_id=dest_uuid,
        )
        if gate["action"] == "skip_verified":
            mapping = gate["mapping"] or {}
            release_create_claim(
                workspace_id, claim_id=claim_id, owner_token=owner_token
            )
            claim_id = None
            repo.update_product_copy_job_item(
                workspace_id,
                item_id,
                {
                    "status": "skipped",
                    "attempt_id": mapping.get("attempt_id"),
                    "destination_item_id": mapping.get("destination_item_id"),
                    "reason": "already_copied_to_destination",
                    "updated_at": _now(),
                },
            )
            return
        if gate["action"] == "needs_reconciliation":
            attempt = gate["attempt"] or {}
            mark_create_claim_uncertain(
                workspace_id,
                claim_id=claim_id,
                owner_token=owner_token,
                attempt_id=str(attempt.get("id") or "") or None,
            )
            claim_id = None  # ownership transferred to uncertain; do not release as free
            repo.update_product_copy_job_item(
                workspace_id,
                item_id,
                {
                    "status": "needs_reconciliation",
                    "attempt_id": attempt.get("id"),
                    "destination_item_id": attempt.get("destination_item_id"),
                    "reason": "stale_claim_requires_reconciliation",
                    "updated_at": _now(),
                },
            )
            return

        source_slug = str(job.get("source_store_slug") or "")
        result = add_product_from_connected(
            workspace_id,
            source_store_id=source_slug,
            daraz_item_id=daraz_item,
            destination_store_ids=[dest_slug],
            execute=True,
            confirm=True,
            allow_duplicates=False,
            claim_gate=False,  # claim already held by this worker
        )
        destinations = result.get("destinations") or []
        dest0 = destinations[0] if destinations else {"status": "NEEDS_ATTENTION", "reason": "no_destination_result"}
        status, attempt_id, dest_item, reason = _map_dest_status(dest0)
        if not product_create_enabled() and status != "verified":
            status = "needs_attention"
            reason = reason or "ALLOW_PRODUCT_CREATE is not enabled"

        # Title-only local-catalog ALREADY_EXISTS is not durable source→dest identity.
        reason_text = str(dest0.get("reason") or "")
        _durable_already = {
            "product_create_attempt_already_verified",
            "already_copied_to_destination",
        }
        if (
            status == "verified"
            and str(dest0.get("status") or "").upper() == "ALREADY_EXISTS"
            and reason_text not in _durable_already
        ):
            status = "needs_attention"
            reason = reason or "possible_destination_match_not_source_identity"

        if status == "verified" and dest_item:
            try:
                record_verified_copy_mapping(
                    workspace_id,
                    source_store_id=source_uuid,
                    source_daraz_item_id=daraz_item,
                    destination_store_id=dest_uuid,
                    destination_item_id=str(dest_item),
                    attempt_id=attempt_id,
                )
            except ValueError:
                pass
            release_create_claim(
                workspace_id, claim_id=claim_id, owner_token=owner_token
            )
            claim_id = None
        elif status == "needs_reconciliation":
            mark_create_claim_uncertain(
                workspace_id,
                claim_id=claim_id,
                owner_token=owner_token,
                attempt_id=attempt_id,
            )
            claim_id = None
        elif status in {"failed", "needs_attention", "skipped"}:
            # Safe to retry later — release claim for another attempt.
            release_create_claim(
                workspace_id, claim_id=claim_id, owner_token=owner_token
            )
            claim_id = None

        repo.update_product_copy_job_item(
            workspace_id,
            item_id,
            {
                "status": status,
                "attempt_id": attempt_id,
                "destination_item_id": dest_item,
                "reason": reason,
                "updated_at": _now(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("bulk copy item failed job=%s item=%s err=%s", job.get("id"), daraz_item, exc)
        open_attempt = find_uncertain_create_attempt(
            workspace_id,
            source_store_id=source_uuid,
            source_daraz_item_id=daraz_item,
            destination_store_id=dest_uuid,
        )
        if open_attempt and claim_id and owner_token:
            # Create may have been submitted — do not free the claim for another CreateProduct.
            mark_create_claim_uncertain(
                workspace_id,
                claim_id=claim_id,
                owner_token=owner_token,
                attempt_id=str(open_attempt.get("id") or "") or None,
            )
            claim_id = None
            repo.update_product_copy_job_item(
                workspace_id,
                item_id,
                {
                    "status": "needs_reconciliation",
                    "attempt_id": open_attempt.get("id"),
                    "destination_item_id": open_attempt.get("destination_item_id"),
                    "reason": f"create_interrupted:{type(exc).__name__}:{exc}"[:240],
                    "updated_at": _now(),
                },
            )
        else:
            if claim_id and owner_token:
                release_create_claim(
                    workspace_id, claim_id=claim_id, owner_token=owner_token
                )
                claim_id = None
            repo.update_product_copy_job_item(
                workspace_id,
                item_id,
                {
                    "status": "failed",
                    "reason": f"{type(exc).__name__}:{exc}"[:240],
                    "updated_at": _now(),
                },
            )
    finally:
        # If we still own a held claim (unexpected path), release for safe retry.
        if claim_id and owner_token:
            try:
                release_create_claim(
                    workspace_id, claim_id=claim_id, owner_token=owner_token
                )
            except Exception:  # noqa: BLE001
                pass
        job_row = repo.get_product_copy_job(workspace_id, str(job["id"]))
        if job_row:
            _recompute_job_counts(repo, job_row)


def _requeue_orphaned_preparing(
    repo: Any, workspace_id: str, job_id: str
) -> None:
    """Under exclusive worker ownership, reclaim preparing left by a crashed worker."""
    preparing = (
        repo.list_product_copy_job_items(
            workspace_id, job_id, page=1, page_size=10_000, status="preparing"
        ).get("items")
        or []
    )
    for item in preparing:
        repo.update_product_copy_job_item(
            workspace_id,
            str(item["id"]),
            {"status": "queued", "updated_at": _now()},
        )


def _run_job_worker(workspace_id: str, job_id: str) -> None:
    repo = get_repo()
    try:
        job = repo.get_product_copy_job(workspace_id, job_id)
        if not job:
            return
        repo.update_product_copy_job(
            workspace_id,
            job_id,
            {"status": "running", "started_at": job.get("started_at") or _now(), "updated_at": _now()},
        )
        # Safe only because spawn sites guarantee one in-process worker per job_id.
        _requeue_orphaned_preparing(repo, workspace_id, job_id)
        dest_slug = str(job.get("destination_store_slug") or "")
        while True:
            batch = repo.list_product_copy_job_items(
                workspace_id, job_id, page=1, page_size=100, status="queued"
            )
            rows = batch.get("items") or []
            if not rows:
                break
            # Claim before fan-out so another worker cannot double-process.
            claimed: list[dict[str, Any]] = []
            for item in rows:
                updated = repo.update_product_copy_job_item(
                    workspace_id,
                    str(item["id"]),
                    {"status": "preparing", "updated_at": _now()},
                )
                if updated and updated.get("status") == "preparing":
                    claimed.append(updated)

            if not claimed:
                break

            workers = min(PRODUCT_BULK_COPY_CONCURRENCY, len(claimed))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [
                    pool.submit(_process_one_item, workspace_id, job, item, dest_slug)
                    for item in claimed
                ]
                for fut in as_completed(futs):
                    try:
                        fut.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.info("bulk copy worker error: %s", exc)

        job = repo.get_product_copy_job(workspace_id, job_id) or job
        job = _recompute_job_counts(repo, job)
        all_items = (
            repo.list_product_copy_job_items(
                workspace_id, job_id, page=1, page_size=10_000
            ).get("items")
            or []
        )
        pending = [
            r
            for r in all_items
            if str(r.get("status") or "") not in _TERMINAL_ITEM_STATUSES
        ]
        if pending:
            # Items still in flight (e.g. a second caller raced this worker) — do not finalize.
            return
        failed = int(job.get("failed_count") or 0)
        attn = int(job.get("needs_attention_count") or 0)
        recon = int(job.get("needs_reconciliation_count") or 0)
        verified = int(job.get("verified_count") or 0)
        skipped = int(job.get("skipped_count") or 0)
        total = int(job.get("total_items") or 0)
        if failed + attn + recon == 0 and verified + skipped >= total:
            status = "completed"
        elif verified == 0 and skipped == 0 and failed + attn + recon > 0:
            status = "failed"
        else:
            status = "partial"
        repo.update_product_copy_job(
            workspace_id,
            job_id,
            {"status": status, "finished_at": _now(), "updated_at": _now()},
        )
    finally:
        with _workers_lock:
            _running_workers.discard(job_id)


def start_bulk_copy_job(
    workspace_id: str,
    *,
    source_store_id: str,
    destination_store_id: str,
    product_item_ids: list[str] | None = None,
    select_all: bool = False,
    exclude_item_ids: list[str] | None = None,
    actor_user_id: str | None = None,
    refresh_catalog: bool = True,
    spawn_worker: bool = True,
) -> dict[str, Any]:
    """Create a durable bulk-copy job and start background processing.

    Set spawn_worker=False in tests to run `_run_job_worker` synchronously.
    """
    source = _resolve_store(workspace_id, source_store_id)
    dest = _resolve_store(workspace_id, destination_store_id)
    if not source:
        raise ValueError("Source store not found")
    if not dest:
        raise ValueError("Destination store not found")
    if str(source["id"]) == str(dest["id"]):
        raise ValueError("Source and destination must be different stores")

    from src.workspace_connections import assert_product_copy_allowed

    perm = assert_product_copy_allowed(
        actor_workspace_id=workspace_id,
        source_store=source,
        destination_store=dest,
    )
    if not perm.get("allowed"):
        raise PermissionError(
            f"copy_permission_denied:{perm.get('reason') or 'copy_products_not_granted'}"
        )

    catalog = discover_source_catalog(
        workspace_id,
        str(source.get("store_id") or source["id"]),
        refresh=refresh_catalog,
        page=1,
        page_size=50,
    )
    eligible = list(catalog.get("eligible_item_ids") or [])
    exclude = {str(x) for x in (exclude_item_ids or []) if str(x).strip()}

    if select_all:
        selected = [i for i in eligible if i not in exclude]
    else:
        requested = [str(x) for x in (product_item_ids or []) if str(x).strip()]
        if not requested:
            raise ValueError("Select at least one product, or use select_all")
        eligible_set = set(eligible)
        selected = [i for i in requested if i in eligible_set and i not in exclude]
        if not selected:
            raise ValueError("No eligible products in selection")

    # Deduplicate while preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for iid in selected:
        if iid not in seen:
            seen.add(iid)
            ordered.append(iid)

    fp = _fingerprint(workspace_id, str(source["id"]), str(dest["id"]), ordered)
    repo = get_repo()
    existing = repo.get_product_copy_job_by_fingerprint(workspace_id, fp)
    if existing and existing.get("status") in _ACTIVE_JOB_STATUSES:
        return {"job": existing, "resumed": True, "duplicate_prevented": True}
    if existing and existing.get("status") in {"completed", "partial"}:
        # Same exact selection already finished — return it (no blind recreate).
        return {"job": existing, "resumed": False, "duplicate_prevented": True}
    if existing and existing.get("status") == "failed":
        # Retire fingerprint so a new identical selection can start without UNIQUE clash.
        repo.update_product_copy_job(
            workspace_id,
            str(existing["id"]),
            {"request_fingerprint": f"{fp}:retired:{existing['id']}", "updated_at": _now()},
        )

    if not product_create_enabled():
        raise ValueError(
            "Product creation is disabled. Enable ALLOW_PRODUCT_CREATE after "
            "supervised proof before starting a bulk copy."
        )

    # Build title lookup from catalog walk
    title_by_item: dict[str, str] = {}
    product_id_by_item: dict[str, str] = {}
    variants_by_item: dict[str, int] = {}
    thumb_by_item: dict[str, str | None] = {}
    walk = 1
    while walk <= 100:
        batch = repo.list_daraz_products(
            workspace_id,
            {
                "store_uuids": [str(source["id"])],
                "page": walk,
                "page_size": 200,
                "sort": "title_asc",
            },
        )
        rows = batch.get("items") or []
        if not rows:
            break
        for p in rows:
            iid = str(p.get("daraz_item_id") or "")
            if iid not in seen:
                continue
            title_by_item[iid] = str(p.get("title_en") or p.get("title") or "")
            product_id_by_item[iid] = str(p.get("id"))
            variants_by_item[iid] = int(p.get("variants_count") or 0)
            imgs = p.get("images_json") or []
            if isinstance(imgs, list) and imgs:
                first = imgs[0]
                thumb_by_item[iid] = (
                    first.get("url") if isinstance(first, dict) else (
                        first if isinstance(first, str) else None
                    )
                )
        if len(rows) < 200:
            break
        walk += 1

    job = repo.create_product_copy_job(
        {
            "id": _uuid(),
            "workspace_id": workspace_id,
            "actor_user_id": actor_user_id,
            "source_store_id": str(source["id"]),
            "destination_store_id": str(dest["id"]),
            "source_store_slug": source.get("store_id"),
            "destination_store_slug": dest.get("store_id"),
            "request_fingerprint": fp,
            "status": "queued",
            "total_items": len(ordered),
            "completed_items": 0,
            "verified_count": 0,
            "failed_count": 0,
            "needs_attention_count": 0,
            "needs_reconciliation_count": 0,
            "skipped_count": 0,
            "excluded_item_ids": sorted(exclude),
            "catalog_incomplete": bool(catalog.get("catalog_incomplete")),
            "catalog_warning": catalog.get("catalog_warning"),
            "created_at": _now(),
            "updated_at": _now(),
        }
    )
    item_rows = [
        {
            "id": _uuid(),
            "job_id": str(job["id"]),
            "workspace_id": workspace_id,
            "source_product_id": product_id_by_item.get(iid),
            "source_daraz_item_id": iid,
            "source_title": title_by_item.get(iid),
            "thumbnail_url": thumb_by_item.get(iid),
            "variants_count": variants_by_item.get(iid),
            "status": "queued",
            "created_at": _now(),
            "updated_at": _now(),
        }
        for iid in ordered
    ]
    repo.create_product_copy_job_items(item_rows)

    if spawn_worker:
        with _workers_lock:
            if str(job["id"]) not in _running_workers:
                _running_workers.add(str(job["id"]))
                t = threading.Thread(
                    target=_run_job_worker,
                    args=(workspace_id, str(job["id"])),
                    daemon=True,
                    name=f"product-copy-{job['id'][:8]}",
                )
                t.start()

    return {
        "job": repo.get_product_copy_job(workspace_id, str(job["id"])) or job,
        "resumed": False,
        "duplicate_prevented": False,
        "catalog_incomplete": bool(catalog.get("catalog_incomplete")),
        "catalog_warning": catalog.get("catalog_warning"),
        "product_create_enabled": product_create_enabled(),
    }


def get_bulk_copy_job(workspace_id: str, job_id: str) -> dict[str, Any] | None:
    repo = get_repo()
    job = repo.get_product_copy_job(workspace_id, job_id)
    if not job:
        return None
    # Restart worker if process restarted while job was queued/running
    if job.get("status") in _ACTIVE_JOB_STATUSES:
        with _workers_lock:
            if str(job_id) not in _running_workers:
                _running_workers.add(str(job_id))
                threading.Thread(
                    target=_run_job_worker,
                    args=(workspace_id, str(job_id)),
                    daemon=True,
                    name=f"product-copy-resume-{job_id[:8]}",
                ).start()
    return job


def list_bulk_copy_job_items(
    workspace_id: str,
    job_id: str,
    *,
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
) -> dict[str, Any]:
    repo = get_repo()
    job = repo.get_product_copy_job(workspace_id, job_id)
    if not job:
        raise LookupError("Job not found")
    result = repo.list_product_copy_job_items(
        workspace_id, job_id, page=page, page_size=page_size, status=status
    )
    for row in result.get("items") or []:
        row["status_label"] = human_item_status(row.get("status"))
    return result


def retry_failed_bulk_copy_items(
    workspace_id: str,
    job_id: str,
    *,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Re-queue failed / needs_attention items that have safe retry attempts."""
    del actor_user_id
    repo = get_repo()
    job = repo.get_product_copy_job(workspace_id, job_id)
    if not job:
        raise LookupError("Job not found")
    if job.get("status") in _ACTIVE_JOB_STATUSES:
        raise ValueError("Job is still running")

    items = repo.list_product_copy_job_items(
        workspace_id, job_id, page=1, page_size=10_000
    )
    requeued = 0
    for row in items.get("items") or []:
        st = str(row.get("status") or "")
        if st not in {"failed", "needs_attention"}:
            continue
        # Prefer attempt-level retry when attempt_id present
        attempt_id = row.get("attempt_id")
        if attempt_id:
            from src.product_create_reconcile import retry_product_create_attempt

            try:
                out = retry_product_create_attempt(
                    workspace_id, str(attempt_id), execute=True
                )
                status, aid, dest_item, reason = _map_dest_status(
                    {
                        "status": out.get("status"),
                        "attempt_id": attempt_id,
                        "item_id": (out.get("attempt") or {}).get("destination_item_id"),
                        "reason": out.get("reason"),
                    }
                )
                repo.update_product_copy_job_item(
                    workspace_id,
                    str(row["id"]),
                    {
                        "status": status,
                        "attempt_id": aid or attempt_id,
                        "destination_item_id": dest_item,
                        "reason": reason,
                        "updated_at": _now(),
                    },
                )
                requeued += 1
                continue
            except Exception:  # noqa: BLE001
                pass
        repo.update_product_copy_job_item(
            workspace_id,
            str(row["id"]),
            {"status": "queued", "reason": None, "updated_at": _now()},
        )
        requeued += 1

    job = _recompute_job_counts(repo, job)
    if requeued:
        repo.update_product_copy_job(
            workspace_id,
            job_id,
            {
                "status": "queued",
                "finished_at": None,
                "error": None,
                "updated_at": _now(),
            },
        )
        with _workers_lock:
            if str(job_id) not in _running_workers:
                _running_workers.add(str(job_id))
                threading.Thread(
                    target=_run_job_worker,
                    args=(workspace_id, str(job_id)),
                    daemon=True,
                    name=f"product-copy-retry-{job_id[:8]}",
                ).start()
    return {
        "job": repo.get_product_copy_job(workspace_id, job_id),
        "requeued": requeued,
    }


def reconcile_bulk_copy_items(
    workspace_id: str,
    job_id: str,
    *,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Reconcile needs_reconciliation items via existing create-attempt reconcile.

    Does not create new products — only resolves uncertain remote outcomes.
    """
    del actor_user_id
    repo = get_repo()
    job = repo.get_product_copy_job(workspace_id, job_id)
    if not job:
        raise LookupError("Job not found")
    if job.get("status") in _ACTIVE_JOB_STATUSES:
        raise ValueError("Job is still running")

    from src.product_create_reconcile import reconcile_product_create_attempt

    items = repo.list_product_copy_job_items(
        workspace_id, job_id, page=1, page_size=10_000
    )
    reconciled = 0
    unresolved = 0
    for row in items.get("items") or []:
        if str(row.get("status") or "") != "needs_reconciliation":
            continue
        attempt_id = row.get("attempt_id")
        if not attempt_id:
            unresolved += 1
            continue
        try:
            out = reconcile_product_create_attempt(workspace_id, str(attempt_id))
            status, aid, dest_item, reason = _map_dest_status(
                {
                    "status": out.get("status") or out.get("attempt_state"),
                    "attempt_id": attempt_id,
                    "item_id": (out.get("attempt") or {}).get("destination_item_id")
                    or out.get("destination_item_id"),
                    "reason": out.get("reason"),
                }
            )
            repo.update_product_copy_job_item(
                workspace_id,
                str(row["id"]),
                {
                    "status": status,
                    "attempt_id": aid or attempt_id,
                    "destination_item_id": dest_item,
                    "reason": reason,
                    "updated_at": _now(),
                },
            )
            reconciled += 1
        except Exception as exc:  # noqa: BLE001
            unresolved += 1
            repo.update_product_copy_job_item(
                workspace_id,
                str(row["id"]),
                {
                    "reason": f"reconcile_failed:{type(exc).__name__}"[:240],
                    "updated_at": _now(),
                },
            )

    job = _recompute_job_counts(repo, job)
    failed = int(job.get("failed_count") or 0)
    attn = int(job.get("needs_attention_count") or 0)
    recon = int(job.get("needs_reconciliation_count") or 0)
    verified = int(job.get("verified_count") or 0)
    skipped = int(job.get("skipped_count") or 0)
    total = int(job.get("total_items") or 0)
    if failed + attn + recon == 0 and verified + skipped >= total:
        status = "completed"
    elif verified == 0 and skipped == 0 and failed + attn + recon > 0:
        status = "failed"
    else:
        status = "partial"
    repo.update_product_copy_job(
        workspace_id,
        job_id,
        {"status": status, "updated_at": _now()},
    )
    return {
        "job": repo.get_product_copy_job(workspace_id, job_id),
        "reconciled": reconciled,
        "unresolved": unresolved,
    }
