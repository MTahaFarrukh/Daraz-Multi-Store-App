"""Small workspace capability layer for current roles."""
from __future__ import annotations

CAPABILITIES = {
    "workspace.read", "store.manage", "product.create", "product.retry",
    "shipping.print", "shipping.reprint", "connections.manage", "settings.manage",
}
_ROLE_CAPS = {
    "owner": CAPABILITIES,
    "admin": CAPABILITIES,
    "manager": {"workspace.read", "product.create", "product.retry", "shipping.print", "shipping.reprint"},
    "member": {"workspace.read", "shipping.print"},
    "viewer": {"workspace.read"},
}

def has_capability(role: str | None, capability: str) -> bool:
    return capability in _ROLE_CAPS.get((role or "").strip().lower(), set())

def require_capability(role: str | None, capability: str) -> None:
    if capability not in CAPABILITIES or not has_capability(role, capability):
        raise PermissionError("Insufficient workspace capability")
