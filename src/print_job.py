"""Per-workspace / per-job print job registry."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from src.config import OUTPUT_DIR
from src.db import get_repo

_lock = threading.Lock()
# Hot cache for progress updates (also persisted to repo).
_jobs: dict[str, dict[str, Any]] = {}


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
        # Also check repo for active jobs
    repo = get_repo()
    # Memory/Postgres: scan is only needed for memory; postgres could query.
    # Lightweight: rely on in-memory for concurrent guard + repo persistence.
    job_id = new_job_id()
    job = {
        "id": job_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "status": "processing",
        "message": "Print job queued…",
        "error": None,
        "result": None,
        "output_path": str(job_pdf_path(workspace_id, job_id)),
        "started_at": _now(),
        "updated_at": _now(),
    }
    _persist(job)
    return job_id


def get_print_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        cached = _jobs.get(str(job_id))
        if cached:
            out = dict(cached)
            if out.get("result"):
                out["result"] = dict(out["result"])
            return out
    return get_repo().get_print_job(job_id)


def require_workspace_job(job_id: str, workspace_id: str) -> dict[str, Any]:
    job = get_print_job(job_id)
    if not job:
        raise LookupError("Print job not found")
    if str(job.get("workspace_id")) != str(workspace_id):
        raise PermissionError("Print job does not belong to this workspace")
    return job


def update_print_job(job_id: str, message: str) -> None:
    job = get_print_job(job_id)
    if not job or job.get("status") != "processing":
        return
    job["message"] = message
    job["updated_at"] = _now()
    _persist(job)


def complete_print_job(job_id: str, result: dict[str, Any]) -> None:
    job = get_print_job(job_id)
    if not job:
        return
    job.update(
        {
            "status": "done",
            "message": "PDF ready",
            "error": None,
            "result": dict(result),
            "updated_at": _now(),
        }
    )
    _persist(job)


def fail_print_job(job_id: str, error: str) -> None:
    job = get_print_job(job_id)
    if not job:
        return
    job.update(
        {
            "status": "error",
            "message": "Print failed",
            "error": error,
            "result": None,
            "updated_at": _now(),
        }
    )
    _persist(job)


def reset_print_job_if_stale(job_id: str, *, max_age_seconds: int = 1800) -> None:
    job = get_print_job(job_id)
    if not job or job.get("status") != "processing":
        return
    started = job.get("started_at")
    if not started:
        return
    try:
        started_dt = datetime.fromisoformat(str(started))
    except ValueError:
        return
    age = (datetime.now(UTC) - started_dt).total_seconds()
    if age > max_age_seconds:
        fail_print_job(job_id, "Previous print job was interrupted. Try again.")


def progress_callback(job_id: str) -> Callable[[str], None]:
    def _cb(message: str) -> None:
        update_print_job(job_id, message)

    return _cb


# --- Backward-compatible shims (unused by new app; kept for old imports in tests) ---

def get_print_job_state() -> dict[str, Any]:
    return {"status": "idle", "message": "", "error": None, "result": None}
