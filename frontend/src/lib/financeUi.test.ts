import { describe, expect, it } from "vitest";
import {
  FINANCE_EMPTY_MESSAGE,
  FINANCE_PARTIAL_WARNING,
  financeNotSynced,
  financePartialWarning,
  formatFinanceAmount,
} from "./financeUi";
import { canReadFinance, canSyncFinance } from "./capabilities";

describe("finance UI", () => {
  it("never shows unknown amounts as Rs 0", () => {
    expect(formatFinanceAmount(null)).toBe("—");
    expect(formatFinanceAmount(undefined)).toBe("—");
    expect(formatFinanceAmount(0)).toContain("0");
  });

  it("detects not-synced state", () => {
    expect(financeNotSynced(null)).toBe(true);
    expect(financeNotSynced({ transaction_count: 0, payout_count: 0 })).toBe(true);
    expect(
      financeNotSynced({ last_synced: "2026-01-01T00:00:00Z", transaction_count: 0 })
    ).toBe(false);
  });

  it("partial sync warning", () => {
    expect(financePartialWarning({ partial: true })).toBe(FINANCE_PARTIAL_WARNING);
    expect(financePartialWarning({ status: "completed" })).toBeNull();
    expect(FINANCE_EMPTY_MESSAGE).toMatch(/not been synced/);
  });

  it("gates Sync Finance by capability", () => {
    const viewer = { role: "viewer", capabilities: ["workspace.read"] };
    const manager = {
      role: "manager",
      capabilities: ["workspace.read", "finance.read"],
    };
    const owner = {
      role: "owner",
      capabilities: ["workspace.read", "finance.read", "finance.sync"],
    };
    expect(canReadFinance(viewer)).toBe(false);
    expect(canSyncFinance(viewer)).toBe(false);
    expect(canReadFinance(manager)).toBe(true);
    expect(canSyncFinance(manager)).toBe(false);
    expect(canSyncFinance(owner)).toBe(true);
  });
});
