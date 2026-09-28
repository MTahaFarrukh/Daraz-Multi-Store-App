"""Workspace-scoped safe audit event helpers."""
from __future__ import annotations
from typing import Any

SENSITIVE_KEYS = {"access_token", "refresh_token", "token", "authorization", "secret", "password"}

def safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    return {str(k): v for k, v in (metadata or {}).items() if str(k).lower() not in SENSITIVE_KEYS}

def audit_event(repo: Any, *, workspace_id: str, actor_user_id: str | None, action: str, entity_type: str, entity_id: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"workspace_id": workspace_id, "actor_user_id": actor_user_id, "action": action, "entity_type": entity_type, "entity_id": entity_id, "metadata": safe_metadata(metadata)}
    if hasattr(repo, "insert_audit_event"):
        return repo.insert_audit_event(payload)
    return payload
