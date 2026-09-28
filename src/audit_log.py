"""Workspace-scoped safe audit event helpers."""

from __future__ import annotations

from typing import Any

SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "token",
    "authorization",
    "secret",
    "password",
    "signed_url",
    "payload",
    "xml",
    "raw_xml",
    "daraz_response",
    "app_secret",
    "client_secret",
    "cookie",
    "set-cookie",
    "customer_name",
    "customer_phone",
    "customer_address",
    "phone",
    "address",
}


def _is_sensitive_key(key: str) -> bool:
    low = key.lower()
    if low in SENSITIVE_KEYS:
        return True
    return any(
        bit in low
        for bit in (
            "token",
            "secret",
            "password",
            "authorization",
            "signed_url",
            "raw_xml",
            "access_token",
            "refresh_token",
        )
    )


def safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Scrub secrets recursively; keep only JSON-safe scalars/objects/lists."""
    return _scrub(metadata or {})


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if _is_sensitive_key(key):
                continue
            out[key] = _scrub(v)
        return out
    if isinstance(value, list):
        return [_scrub(v) for v in value[:50]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > 500:
            return value[:500] + "…"
        return value
    return str(value)[:240]


def audit_event(
    repo: Any,
    *,
    workspace_id: str,
    actor_user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "workspace_id": workspace_id,
        "actor_user_id": actor_user_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "metadata": safe_metadata(metadata),
    }
    if hasattr(repo, "insert_audit_event"):
        return repo.insert_audit_event(payload)
    return payload


def sanitize_audit_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    meta = row.get("metadata")
    return {
        "id": row.get("id"),
        "workspace_id": row.get("workspace_id"),
        "actor_user_id": row.get("actor_user_id"),
        "action": row.get("action"),
        "entity_type": row.get("entity_type"),
        "entity_id": row.get("entity_id"),
        "metadata": safe_metadata(meta if isinstance(meta, dict) else {}),
        "created_at": row.get("created_at"),
    }
