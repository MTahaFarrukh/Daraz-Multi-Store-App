import { describe, expect, it } from "vitest";
import {
  canManageConnections,
  canReadFinance,
  canSyncFinance,
  canViewAudit,
  formatAuditAction,
  hasCapability,
} from "@/lib/capabilities";

describe("capabilities helpers", () => {
  it("uses capability list when present", () => {
    const ws = { role: "viewer", capabilities: ["workspace.read"] };
    expect(hasCapability(ws, "workspace.read")).toBe(true);
    expect(hasCapability(ws, "store.manage")).toBe(false);
    expect(canManageConnections(ws)).toBe(false);
  });

  it("owner/admin can manage connections and audit", () => {
    const owner = {
      role: "owner",
      capabilities: ["workspace.read", "connections.manage", "settings.manage"],
    };
    expect(canManageConnections(owner)).toBe(true);
    expect(canViewAudit(owner)).toBe(true);
  });

  it("finance read/sync gating", () => {
    expect(
      canSyncFinance({
        role: "owner",
        capabilities: ["finance.read", "finance.sync"],
      })
    ).toBe(true);
    expect(canReadFinance({ role: "manager", capabilities: ["finance.read"] })).toBe(
      true
    );
    expect(canSyncFinance({ role: "manager", capabilities: ["finance.read"] })).toBe(
      false
    );
  });

  it("formats audit actions for UI", () => {
    expect(formatAuditAction("product.create.verified").toLowerCase()).toContain(
      "product"
    );
  });
});
