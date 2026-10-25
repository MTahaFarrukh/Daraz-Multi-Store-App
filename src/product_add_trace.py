"""Safe correlation / stage tracing for Add Product (no secrets in logs or UI)."""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_trace_var: ContextVar["ProductAddTrace | None"] = ContextVar(
    "product_add_trace", default=None
)

_SAFE_KEYS = frozenset(
    {
        "stage",
        "at_ms",
        "store_id",
        "status",
        "reason",
        "attempt_id",
        "gate_on",
        "execute",
        "edit_before",
        "dest_count",
        "created_count",
        "failed_count",
        "needs_attention_count",
        "elapsed_ms",
        "source",
        "http_status",
        "error_class",
        "message",
    }
)


def current_trace() -> "ProductAddTrace | None":
    return _trace_var.get()


def bind_trace(trace: "ProductAddTrace | None"):
    return _trace_var.set(trace)


def reset_trace(token) -> None:
    _trace_var.reset(token)


def _safe_fields(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in _SAFE_KEYS:
            continue
        if value is None:
            continue
        if isinstance(value, (bool, int, float)):
            out[key] = value
        else:
            text = str(value)
            if len(text) > 180:
                text = text[:177] + "…"
            # Never echo credential-looking fragments
            low = text.lower()
            if any(s in low for s in ("bearer ", "token=", "secret", "password", "cookie")):
                out[key] = "[redacted]"
            else:
                out[key] = text
    return out


@dataclass
class ProductAddTrace:
    correlation_id: str
    workspace_short: str
    stages: list[dict[str, Any]] = field(default_factory=list)
    t0: float = field(default_factory=time.perf_counter)

    @classmethod
    def create(cls, workspace_id: str) -> "ProductAddTrace":
        wid = str(workspace_id or "")
        return cls(
            correlation_id=str(uuid.uuid4()),
            workspace_short=(wid[:8] if wid else "unknown"),
        )

    def stage(self, name: str, **fields: Any) -> dict[str, Any]:
        entry = {
            "stage": name,
            "at_ms": round((time.perf_counter() - self.t0) * 1000, 1),
            **_safe_fields(fields),
        }
        self.stages.append(entry)
        extras = " ".join(f"{k}={v}" for k, v in entry.items() if k not in {"stage", "at_ms"})
        logger.info(
            "product_add cid=%s ws=%s stage=%s at_ms=%.1f %s",
            self.correlation_id,
            self.workspace_short,
            name,
            entry["at_ms"],
            extras,
        )
        return entry

    def public_history(self) -> list[dict[str, Any]]:
        return list(self.stages)

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.t0) * 1000, 1)


def safe_error_detail(message: str, *, diagnostic_id: str, status_code: int = 400) -> dict[str, Any]:
    """Browser-safe error payload — never include stack traces."""
    text = str(message or "Request failed").strip() or "Request failed"
    if len(text) > 240:
        text = text[:237] + "…"
    return {
        "message": text,
        "diagnostic_id": diagnostic_id,
        "status_code": status_code,
    }
