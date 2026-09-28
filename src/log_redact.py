"""Log / exception message redaction for secrets and signed URLs."""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "authorization",
        "password",
        "secret",
        "api_key",
        "app_secret",
        "supabase_secret_key",
        "daraz_token_key",
        "token",
        "xml",
        "payload_xml",
        "create_product_xml",
    }
)

_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.I)
_QUERY_SECRET_RE = re.compile(
    r"([?&](?:access_token|refresh_token|token|signature|sign|key)=)([^&\s]+)",
    re.I,
)


def redact_string(value: str) -> str:
    s = _BEARER_RE.sub(r"\1[REDACTED]", value)
    s = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", s)
    return s


def redact_mapping(data: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[truncated]"
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            key = str(k)
            if key.lower() in _SENSITIVE_KEYS or "token" in key.lower() or "secret" in key.lower():
                out[key] = "[REDACTED]"
            else:
                out[key] = redact_mapping(v, depth=depth + 1)
        return out
    if isinstance(data, list):
        return [redact_mapping(x, depth=depth + 1) for x in data[:50]]
    if isinstance(data, str):
        return redact_string(data)
    return data


class RedactingFilter:
    """logging.Filter that redacts common secret patterns from LogRecord messages."""

    def filter(self, record: Any) -> bool:  # noqa: A003 — logging API
        try:
            if isinstance(record.msg, str):
                record.msg = redact_string(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = redact_mapping(record.args)
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        redact_string(a) if isinstance(a, str) else a for a in record.args
                    )
        except Exception:  # noqa: BLE001
            pass
        return True
