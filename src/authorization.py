"""Workspace capability layer — centralized RBAC for MultiStore."""

from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException

CAPABILITIES = frozenset(
    {
        "workspace.read",
        "store.manage",
        "product.create",
        "product.retry",
        "shipping.print",
        "shipping.reprint",
        "connections.manage",
        "settings.manage",
        "finance.read",
        "finance.sync",
    }
)

# Conservative role → capability matrix.
# ``staff`` is a legacy DB role alias mapped to manager operational caps.
_ROLE_CAPS: dict[str, frozenset[str]] = {
    "owner": CAPABILITIES,
    "admin": CAPABILITIES,
    "manager": frozenset(
        {
            "workspace.read",
            "product.create",
            "product.retry",
            "shipping.print",
            "shipping.reprint",
            "finance.read",
        }
    ),
    "staff": frozenset(
        {
            "workspace.read",
            "product.create",
            "product.retry",
            "shipping.print",
            "shipping.reprint",
            "finance.read",
        }
    ),
    "member": frozenset({"workspace.read", "shipping.print"}),
    "viewer": frozenset({"workspace.read"}),
}

ROLE_CAPABILITIES = _ROLE_CAPS


def normalize_role(role: str | None) -> str:
    return (role or "").strip().lower()


def capabilities_for_role(role: str | None) -> frozenset[str]:
    return _ROLE_CAPS.get(normalize_role(role), frozenset())


def has_capability(role: str | None, capability: str) -> bool:
    if capability not in CAPABILITIES:
        return False
    return capability in capabilities_for_role(role)


def require_capability(role: str | None, capability: str) -> None:
    if not has_capability(role, capability):
        raise PermissionError("Insufficient workspace capability")


def can_view_audit(role: str | None) -> bool:
    """Audit log read is owner/admin by default (settings.manage holders)."""
    return has_capability(role, "settings.manage")


def enforce_capability(role: str | None, capability: str) -> None:
    """Raise HTTP 403 — use inside route bodies."""
    try:
        require_capability(role, capability)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def require_caps(capability: str) -> Callable:
    """FastAPI dependency factory — backend-authoritative capability gate."""
    from src.auth import WorkspaceContext, get_workspace_context

    def _dependency(
        ctx: WorkspaceContext = Depends(get_workspace_context),
    ) -> WorkspaceContext:
        enforce_capability(ctx.role, capability)
        return ctx

    _dependency.__name__ = f"require_caps_{capability.replace('.', '_')}"
    return _dependency
