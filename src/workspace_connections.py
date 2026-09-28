"""Trusted workspace connections — permission foundation only (no cross-WS copy)."""

from __future__ import annotations

import secrets
import string
from typing import Any

from src.audit_log import audit_event
from src.db.repo import get_repo

STATUSES = frozenset({"PENDING", "ACCEPTED", "REJECTED", "REVOKED"})


class ConnectionError(ValueError):
    def __init__(self, message: str, *, code: str = "connection_error"):
        super().__init__(message)
        self.code = code


def _new_connection_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    # Non-secret invite code — discoverable only when shared by owner.
    return "WS-" + "".join(secrets.choice(alphabet) for _ in range(10))


def ensure_workspace_connection_code(workspace_id: str) -> str:
    repo = get_repo()
    ws = repo.get_workspace(workspace_id) if hasattr(repo, "get_workspace") else None
    code = (ws or {}).get("connection_code") if isinstance(ws, dict) else None
    if code:
        return str(code)
    code = _new_connection_code()
    if hasattr(repo, "set_workspace_connection_code"):
        repo.set_workspace_connection_code(workspace_id, code)
    return code


def resolve_target_workspace(target_code: str) -> dict[str, Any] | None:
    repo = get_repo()
    code = (target_code or "").strip().upper()
    if not code:
        return None
    if hasattr(repo, "get_workspace_by_connection_code"):
        return repo.get_workspace_by_connection_code(code)
    return None


def public_workspace_label(ws: dict[str, Any] | None) -> dict[str, Any]:
    if not ws:
        return {"id": None, "name": None, "connection_code": None}
    return {
        "id": ws.get("id"),
        "name": ws.get("name"),
        "connection_code": ws.get("connection_code"),
    }


def request_connection(
    *,
    source_workspace_id: str,
    actor_user_id: str,
    target_connection_code: str,
) -> dict[str, Any]:
    repo = get_repo()
    ensure_workspace_connection_code(source_workspace_id)
    target = resolve_target_workspace(target_connection_code)
    if not target:
        raise ConnectionError("Unknown connection code", code="target_not_found")
    target_id = str(target["id"])
    if target_id == str(source_workspace_id):
        raise ConnectionError("Cannot connect a workspace to itself", code="self_connect")

    existing = repo.get_connection_between(source_workspace_id, target_id)
    if existing and existing.get("status") == "PENDING":
        raise ConnectionError(
            "A pending connection request already exists",
            code="duplicate_pending",
        )
    if existing and existing.get("status") == "ACCEPTED":
        raise ConnectionError(
            "Workspaces are already connected",
            code="already_connected",
        )

    if existing and existing.get("status") in {"REJECTED", "REVOKED"}:
        row = repo.update_connection(
            existing["id"],
            status="PENDING",
            view_products=False,
            copy_products=False,
            requested_by_user_id=actor_user_id,
            responded_by_user_id=None,
        )
    else:
        row = repo.create_connection(
            {
                "source_workspace_id": source_workspace_id,
                "destination_workspace_id": target_id,
                "status": "PENDING",
                "view_products": False,
                "copy_products": False,
                "requested_by_user_id": actor_user_id,
            }
        )

    audit_event(
        repo,
        workspace_id=source_workspace_id,
        actor_user_id=actor_user_id,
        action="connection.requested",
        entity_type="trusted_workspace_connection",
        entity_id=str(row["id"]),
        metadata={
            "connection_id": str(row["id"]),
            "target_workspace_id": target_id,
            "status": "PENDING",
        },
    )
    # Target-side visibility of inbound request (no private source metadata).
    audit_event(
        repo,
        workspace_id=target_id,
        actor_user_id=actor_user_id,
        action="connection.requested",
        entity_type="trusted_workspace_connection",
        entity_id=str(row["id"]),
        metadata={
            "connection_id": str(row["id"]),
            "source_workspace_id": source_workspace_id,
            "status": "PENDING",
            "direction": "incoming",
        },
    )
    return _serialize(row, viewer_workspace_id=source_workspace_id)


def list_connections(workspace_id: str) -> dict[str, Any]:
    repo = get_repo()
    ensure_workspace_connection_code(workspace_id)
    rows = repo.list_connections_for_workspace(workspace_id)
    incoming: list[dict[str, Any]] = []
    outgoing: list[dict[str, Any]] = []
    connected: list[dict[str, Any]] = []
    for row in rows:
        item = _serialize(row, viewer_workspace_id=workspace_id)
        status = str(row.get("status") or "")
        is_source = str(row.get("source_workspace_id")) == str(workspace_id)
        if status == "PENDING":
            if is_source:
                outgoing.append(item)
            else:
                incoming.append(item)
        elif status == "ACCEPTED":
            connected.append(item)
        elif status in {"REJECTED", "REVOKED"} and is_source:
            # Keep recent outbound rejects/revokes visible lightly
            outgoing.append(item)
    ws = None
    if hasattr(repo, "get_workspace"):
        ws = repo.get_workspace(workspace_id)
    return {
        "workspace": public_workspace_label(ws),
        "incoming": incoming,
        "outgoing": outgoing,
        "connected": connected,
    }


def accept_connection(
    *,
    workspace_id: str,
    connection_id: str,
    actor_user_id: str,
) -> dict[str, Any]:
    repo = get_repo()
    row = repo.get_connection(connection_id)
    if not row:
        raise ConnectionError("Connection not found", code="not_found")
    if str(row.get("destination_workspace_id")) != str(workspace_id):
        raise ConnectionError(
            "Only the target workspace can accept", code="not_target"
        )
    if str(row.get("source_workspace_id")) == str(workspace_id):
        raise ConnectionError(
            "Requester cannot accept their own request", code="requester_cannot_accept"
        )
    if row.get("status") != "PENDING":
        raise ConnectionError(
            f"Connection is {row.get('status')}, not PENDING", code="invalid_state"
        )
    updated = repo.update_connection(
        connection_id,
        status="ACCEPTED",
        responded_by_user_id=actor_user_id,
        # Permissions remain false until explicitly toggled.
        view_products=False,
        copy_products=False,
    )
    _audit_both(
        updated,
        actor_user_id=actor_user_id,
        action="connection.accepted",
        extra={"status": "ACCEPTED"},
    )
    return _serialize(updated, viewer_workspace_id=workspace_id)


def reject_connection(
    *,
    workspace_id: str,
    connection_id: str,
    actor_user_id: str,
) -> dict[str, Any]:
    repo = get_repo()
    row = repo.get_connection(connection_id)
    if not row:
        raise ConnectionError("Connection not found", code="not_found")
    if str(row.get("destination_workspace_id")) != str(workspace_id):
        raise ConnectionError(
            "Only the target workspace can reject", code="not_target"
        )
    if row.get("status") != "PENDING":
        raise ConnectionError(
            f"Connection is {row.get('status')}, not PENDING", code="invalid_state"
        )
    updated = repo.update_connection(
        connection_id,
        status="REJECTED",
        responded_by_user_id=actor_user_id,
        view_products=False,
        copy_products=False,
    )
    _audit_both(
        updated,
        actor_user_id=actor_user_id,
        action="connection.rejected",
        extra={"status": "REJECTED"},
    )
    return _serialize(updated, viewer_workspace_id=workspace_id)


def revoke_connection(
    *,
    workspace_id: str,
    connection_id: str,
    actor_user_id: str,
) -> dict[str, Any]:
    repo = get_repo()
    row = repo.get_connection(connection_id)
    if not row:
        raise ConnectionError("Connection not found", code="not_found")
    src = str(row.get("source_workspace_id"))
    dst = str(row.get("destination_workspace_id"))
    if str(workspace_id) not in {src, dst}:
        raise ConnectionError("Not a party to this connection", code="forbidden")
    if row.get("status") == "PENDING" and str(workspace_id) == src:
        # Outgoing cancel
        updated = repo.update_connection(
            connection_id,
            status="REVOKED",
            responded_by_user_id=actor_user_id,
            view_products=False,
            copy_products=False,
        )
    elif row.get("status") in {"ACCEPTED", "PENDING"}:
        updated = repo.update_connection(
            connection_id,
            status="REVOKED",
            responded_by_user_id=actor_user_id,
            view_products=False,
            copy_products=False,
        )
    else:
        raise ConnectionError(
            f"Cannot revoke connection in state {row.get('status')}",
            code="invalid_state",
        )
    _audit_both(
        updated,
        actor_user_id=actor_user_id,
        action="connection.revoked",
        extra={"status": "REVOKED", "view_products": False, "copy_products": False},
    )
    return _serialize(updated, viewer_workspace_id=workspace_id)


def update_permissions(
    *,
    workspace_id: str,
    connection_id: str,
    actor_user_id: str,
    view_products: bool | None = None,
    copy_products: bool | None = None,
) -> dict[str, Any]:
    repo = get_repo()
    row = repo.get_connection(connection_id)
    if not row:
        raise ConnectionError("Connection not found", code="not_found")
    # Only the destination (grantor) may change permissions after accept.
    if str(row.get("destination_workspace_id")) != str(workspace_id):
        raise ConnectionError(
            "Only the accepting workspace can change permissions",
            code="not_grantor",
        )
    if row.get("status") != "ACCEPTED":
        raise ConnectionError(
            "Permissions can only be changed on ACCEPTED connections",
            code="invalid_state",
        )
    fields: dict[str, Any] = {}
    if view_products is not None:
        fields["view_products"] = bool(view_products)
    if copy_products is not None:
        fields["copy_products"] = bool(copy_products)
    if not fields:
        raise ConnectionError("No permission changes provided", code="empty_patch")
    updated = repo.update_connection(connection_id, **fields)
    _audit_both(
        updated,
        actor_user_id=actor_user_id,
        action="connection.permissions.changed",
        extra={
            "view_products": updated.get("view_products"),
            "copy_products": updated.get("copy_products"),
            "status": updated.get("status"),
        },
    )
    return _serialize(updated, viewer_workspace_id=workspace_id)


def connection_allows_copy(source_workspace_id: str, destination_workspace_id: str) -> bool:
    """Future Product Copy gate — foundation only."""
    repo = get_repo()
    row = repo.get_connection_between(source_workspace_id, destination_workspace_id)
    if not row:
        return False
    return (
        row.get("status") == "ACCEPTED"
        and bool(row.get("copy_products")) is True
    )


def connection_allows_view_products(
    source_workspace_id: str, destination_workspace_id: str
) -> bool:
    repo = get_repo()
    row = repo.get_connection_between(source_workspace_id, destination_workspace_id)
    if not row:
        return False
    return (
        row.get("status") == "ACCEPTED"
        and bool(row.get("view_products")) is True
    )


def _audit_both(
    row: dict[str, Any] | None,
    *,
    actor_user_id: str,
    action: str,
    extra: dict[str, Any],
) -> None:
    if not row:
        return
    repo = get_repo()
    meta_base = {
        "connection_id": str(row.get("id")),
        **extra,
    }
    for wid, peer in (
        (str(row.get("source_workspace_id")), str(row.get("destination_workspace_id"))),
        (str(row.get("destination_workspace_id")), str(row.get("source_workspace_id"))),
    ):
        audit_event(
            repo,
            workspace_id=wid,
            actor_user_id=actor_user_id,
            action=action,
            entity_type="trusted_workspace_connection",
            entity_id=str(row.get("id")),
            metadata={**meta_base, "peer_workspace_id": peer},
        )


def _serialize(row: dict[str, Any], *, viewer_workspace_id: str) -> dict[str, Any]:
    repo = get_repo()
    src_id = str(row.get("source_workspace_id"))
    dst_id = str(row.get("destination_workspace_id"))
    viewer = str(viewer_workspace_id)
    direction = "outgoing" if src_id == viewer else "incoming"
    peer_id = dst_id if src_id == viewer else src_id
    peer = None
    if hasattr(repo, "get_workspace"):
        peer = repo.get_workspace(peer_id)
    status = str(row.get("status") or "")
    perms_live = status == "ACCEPTED"
    return {
        "id": row.get("id"),
        "status": status,
        "direction": direction,
        "peer_workspace": public_workspace_label(peer),
        "view_products": bool(row.get("view_products")) if perms_live else False,
        "copy_products": bool(row.get("copy_products")) if perms_live else False,
        "requested_by_user_id": row.get("requested_by_user_id"),
        "responded_by_user_id": row.get("responded_by_user_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
