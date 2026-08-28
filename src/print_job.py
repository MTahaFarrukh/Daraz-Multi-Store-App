"""In-memory print job state for long-running label PDF builds."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any, Callable

_lock = threading.Lock()
_state: dict[str, Any] = {
    "status": "idle",
    "message": "",
    "error": None,
    "result": None,
    "started_at": None,
    "updated_at": None,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def get_print_job_state() -> dict[str, Any]:
    with _lock:
        out = dict(_state)
        if out.get("result"):
            out["result"] = dict(out["result"])
        return out


def begin_print_job() -> bool:
    """Return False if a job is already running."""
    with _lock:
        if _state["status"] == "processing":
            return False
        _state.update(
            {
                "status": "processing",
                "message": "Starting print job…",
                "error": None,
                "result": None,
                "started_at": _now(),
                "updated_at": _now(),
            }
        )
        return True


def update_print_job(message: str) -> None:
    with _lock:
        if _state["status"] != "processing":
            return
        _state["message"] = message
        _state["updated_at"] = _now()


def complete_print_job(result: dict[str, Any]) -> None:
    with _lock:
        _state.update(
            {
                "status": "done",
                "message": "PDF ready",
                "error": None,
                "result": dict(result),
                "updated_at": _now(),
            }
        )


def fail_print_job(error: str) -> None:
    with _lock:
        _state.update(
            {
                "status": "error",
                "message": "Print failed",
                "error": error,
                "result": None,
                "updated_at": _now(),
            }
        )


def reset_print_job_if_stale(*, max_age_seconds: int = 1800) -> None:
    """Clear stuck jobs after a crash (best-effort)."""
    with _lock:
        if _state["status"] != "processing":
            return
        started = _state.get("started_at")
        if not started:
            return
        try:
            started_dt = datetime.fromisoformat(str(started))
        except ValueError:
            return
        age = (datetime.now(UTC) - started_dt).total_seconds()
        if age > max_age_seconds:
            _state.update(
                status="error",
                message="Print job timed out",
                error="Previous print job was interrupted. Try again.",
                result=None,
                updated_at=_now(),
            )


def progress_callback() -> Callable[[str], None]:
    return update_print_job
