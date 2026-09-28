"""Print job registry with worker ownership, heartbeat, and coalesced progress."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from src.config import OUTPUT_DIR
from src.db import get_repo

_lock = threading.Lock()
# Hot cache for progress updates (also persisted to repo).
_jobs: dict[str, dict[str, Any]] = {}
# Local worker ids owned by this process (survives only while thread is alive).
_local_workers: dict[str, str] = {}  # worker_id → job_id

# Meaningful stages only (coalesce per-target noise).
STAGE_PREPARING = "Preparing"
STAGE_FETCHING = "Fetching labels"
STAGE_PROCESSING = "Processing documents"
STAGE_MERGING = "Merging PDF"
STAGE_SAVING = "Saving history"
STAGE_COMPLETED = "Completed"
STAGE_RECOVERY = "RECOVERY_REQUIRED"

HEARTBEAT_STALE_SECONDS = 120
PROGRESS_PERSIST_MIN_SECONDS = 1.5


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_job_id() -> str:
    return str(uuid.uuid4())


def job_output_dir(workspace_id: str, job_id: str) -> Path:
    path = OUTPUT_DIR / str(workspace_id) / str(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_pdf_path(workspace_id: str, job_id: str) -> Path:
    return job_output_dir(workspace_id, job_id) / "combined-labels.pdf"


def _persist(job: dict[str, Any]) -> None:
    get_repo().save_print_job(job)
    with _lock:
        _jobs[str(job["id"])] = dict(job)
        if job.get("result"):
            _jobs[str(job["id"])]["result"] = dict(job["result"])


def begin_print_job(*, workspace_id: str, user_id: str) -> str:
    """Start a new print job for this workspace. One active job per workspace."""
    with _lock:
        for job in _jobs.values():
            if (
                job.get("workspace_id") == workspace_id
                and job.get("status") == "processing"
            ):
                raise RuntimeError("A print job is already running for this workspace")
    job_id = new_job_id()
    worker_id = str(uuid.uuid4())
    now = _now()
    job = {
        "id": job_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "status": "processing",
        "message": "Print job queued…",
        "error": None,
        "result": None,
        "output_path": str(job_pdf_path(workspace_id, job_id)),
        "started_at": now,
        "updated_at": now,
        "worker_id": worker_id,
        "worker_started_at": now,
        "heartbeat_at": now,
        "processing_stage": STAGE_PREPARING,
        "interrupted_at": None,
        "progress": {"completed": 0, "total": 0, "failed": 0},
    }
    with _lock:
        _local_workers[worker_id] = job_id
    _persist(job)
    return job_id


def get_print_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        cached = _jobs.get(str(job_id))
        if cached:
            out = dict(cached)
            if out.get("result"):
                out["result"] = dict(out["result"])
            if out.get("progress"):
                out["progress"] = dict(out["progress"])
            return out
    return get_repo().get_print_job(job_id)


def require_workspace_job(job_id: str, workspace_id: str) -> dict[str, Any]:
    job = get_print_job(job_id)
    if not job:
        raise LookupError("Print job not found")
    if str(job.get("workspace_id")) != str(workspace_id):
        raise PermissionError("Print job does not belong to this workspace")
    return job


def _clear_local_worker(job: dict[str, Any]) -> None:
    wid = job.get("worker_id")
    if not wid:
        return
    with _lock:
        if _local_workers.get(str(wid)) == str(job.get("id")):
            _local_workers.pop(str(wid), None)


def update_print_job(
    job_id: str,
    message: str,
    *,
    stage: str | None = None,
    progress: dict[str, Any] | None = None,
    force_persist: bool = False,
) -> None:
    job = get_print_job(job_id)
    if not job or job.get("status") != "processing":
        return
    now = _now()
    stage_changed = bool(stage and stage != job.get("processing_stage"))
    job["message"] = message
    job["updated_at"] = now
    job["heartbeat_at"] = now
    if stage:
        job["processing_stage"] = stage
    if progress is not None:
        job["progress"] = dict(progress)
    # Coalesce: persist on stage change, force, or min interval
    last = float(job.get("_last_persist_mono") or 0)
    elapsed = time.monotonic() - last
    if force_persist or stage_changed or elapsed >= PROGRESS_PERSIST_MIN_SECONDS:
        job["_last_persist_mono"] = time.monotonic()
        _persist(job)
    else:
        # Keep hot cache fresh without hammering Postgres
        with _lock:
            cached = dict(job)
            if cached.get("result"):
                cached["result"] = dict(cached["result"])
            _jobs[str(job_id)] = cached


def touch_print_job_heartbeat(job_id: str) -> None:
    job = get_print_job(job_id)
    if not job or job.get("status") != "processing":
        return
    now = _now()
    job["heartbeat_at"] = now
    job["updated_at"] = now
    _persist(job)


def complete_print_job(job_id: str, result: dict[str, Any]) -> None:
    job = get_print_job(job_id)
    if not job:
        return
    now = _now()
    job.update(
        {
            "status": "done",
            "message": "PDF ready",
            "error": None,
            "result": dict(result),
            "updated_at": now,
            "heartbeat_at": now,
            "processing_stage": STAGE_COMPLETED,
            "worker_id": None,
            "interrupted_at": None,
        }
    )
    _clear_local_worker(job)
    _persist(job)


def fail_print_job(job_id: str, error: str) -> None:
    job = get_print_job(job_id)
    if not job:
        return
    now = _now()
    job.update(
        {
            "status": "error",
            "message": "Print failed",
            "error": error,
            "result": None,
            "updated_at": now,
            "heartbeat_at": now,
            "worker_id": None,
        }
    )
    _clear_local_worker(job)
    _persist(job)


def mark_print_job_interrupted(
    job_id: str,
    *,
    reason: str = "Print worker interrupted (stale heartbeat). Retry remaining targets.",
) -> None:
    """Mark a stuck processing job as interrupted / recovery-required.

    Does NOT replay PrintAWB. Proven successes remain in history; client retries
    only remaining / failed targets.
    """
    job = get_print_job(job_id)
    if not job or job.get("status") != "processing":
        return
    now = _now()
    job.update(
        {
            "status": "interrupted",
            "message": "Print interrupted — recovery required",
            "error": reason,
            "updated_at": now,
            "heartbeat_at": now,
            "processing_stage": STAGE_RECOVERY,
            "interrupted_at": now,
            "worker_id": None,
        }
    )
    _clear_local_worker(job)
    _persist(job)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def local_worker_active(worker_id: str | None, job_id: str | None = None) -> bool:
    if not worker_id:
        return False
    with _lock:
        owned = _local_workers.get(str(worker_id))
        if owned is None:
            return False
        if job_id is not None and str(owned) != str(job_id):
            return False
        return True


def reset_print_job_if_stale(
    job_id: str,
    *,
    max_age_seconds: int = 1800,
    heartbeat_stale_seconds: int = HEARTBEAT_STALE_SECONDS,
) -> None:
    """Recover stuck Starting/Printing jobs without auto-replaying PrintAWB.

    Marks INTERRUPTED / RECOVERY_REQUIRED when heartbeat is stale and no matching
    local active worker exists. Absolute max_age on started_at remains a backstop.
    """
    job = get_print_job(job_id)
    if not job or job.get("status") != "processing":
        return

    worker_id = job.get("worker_id")
    if local_worker_active(worker_id, job_id):
        return

    now = datetime.now(UTC)
    heartbeat = (
        _parse_ts(job.get("heartbeat_at"))
        or _parse_ts(job.get("updated_at"))
        or _parse_ts(job.get("started_at"))
    )
    started = _parse_ts(job.get("started_at"))

    stale_heartbeat = False
    if heartbeat is not None:
        stale_heartbeat = (now - heartbeat).total_seconds() > heartbeat_stale_seconds

    absolute_stale = False
    if started is not None:
        absolute_stale = (now - started).total_seconds() > max_age_seconds

    if stale_heartbeat or absolute_stale:
        mark_print_job_interrupted(job_id)


def progress_callback(job_id: str) -> Callable[..., None]:
    """Coalesced progress writer: stage changes + bounded interval only."""

    def _cb(
        message: str,
        *,
        stage: str | None = None,
        progress: dict[str, Any] | None = None,
        force: bool = False,
    ) -> None:
        # Infer stage from common message prefixes when not provided
        inferred = stage
        if inferred is None:
            low = (message or "").lower()
            if "prepar" in low:
                inferred = STAGE_PREPARING
            elif "gather" in low or "printawb" in low or "fetch" in low or "bulk" in low:
                inferred = STAGE_FETCHING
            elif "convert" in low or "process" in low:
                inferred = STAGE_PROCESSING
            elif "combin" in low or "merg" in low:
                inferred = STAGE_MERGING
            elif "histor" in low or "saving" in low or "record" in low:
                inferred = STAGE_SAVING
        update_print_job(
            job_id,
            message,
            stage=inferred,
            progress=progress,
            force_persist=force,
        )

    return _cb


# --- Backward-compatible shims (unused by new app; kept for old imports in tests) ---

def get_print_job_state() -> dict[str, Any]:
    return {"status": "idle", "message": "", "error": None, "result": None}
