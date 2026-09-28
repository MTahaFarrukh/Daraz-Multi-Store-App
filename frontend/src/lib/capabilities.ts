/** Capability helpers for UI gating (backend remains authoritative). */

export type WorkspaceCaps = {
  role?: string | null;
  capabilities?: string[] | null;
};

export function hasCapability(
  workspace: WorkspaceCaps | null | undefined,
  capability: string
): boolean {
  const caps = workspace?.capabilities;
  if (Array.isArray(caps) && caps.length) {
    return caps.includes(capability);
  }
  // Fallback from role when capabilities missing (older sessions).
  const role = String(workspace?.role || "").toLowerCase();
  if (role === "owner" || role === "admin") return true;
  if (capability === "workspace.read") return Boolean(role);
  if (capability === "finance.read" && (role === "manager" || role === "staff")) {
    return true;
  }
  return false;
}

export function canManageConnections(workspace: WorkspaceCaps | null | undefined): boolean {
  return hasCapability(workspace, "connections.manage");
}

export function canViewAudit(workspace: WorkspaceCaps | null | undefined): boolean {
  return hasCapability(workspace, "settings.manage");
}

export function canReadFinance(workspace: WorkspaceCaps | null | undefined): boolean {
  return hasCapability(workspace, "finance.read");
}

export function canSyncFinance(workspace: WorkspaceCaps | null | undefined): boolean {
  return hasCapability(workspace, "finance.sync");
}

export function formatAuditAction(action: string): string {
  return String(action || "")
    .replace(/\./g, " · ")
    .replace(/_/g, " ");
}
