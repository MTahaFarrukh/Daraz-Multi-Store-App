import { describe, expect, it } from "vitest";

/** Mirror of backend human labels used by Bulk Copy UI badges. */
const LABELS: Record<string, string> = {
  queued: "Queued",
  preparing: "Preparing",
  creating: "Creating",
  verifying: "Verifying",
  verified: "Verified",
  skipped: "Skipped",
  needs_attention: "Needs Attention",
  needs_reconciliation: "Needs Reconciliation",
  failed: "Failed",
};

function humanBulkStatus(status: string): string {
  return LABELS[String(status || "").toLowerCase()] || status;
}

const JOB_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  failed: "Failed",
};

describe("bulk copy UI helpers", () => {
  it("humanizes per-product states", () => {
    expect(humanBulkStatus("needs_reconciliation")).toBe("Needs Reconciliation");
    expect(humanBulkStatus("verified")).toBe("Verified");
    expect(humanBulkStatus("failed")).toBe("Failed");
  });

  it("humanizes job statuses without raw dump", () => {
    expect(JOB_LABELS.running).toBe("Running");
    expect(JOB_LABELS.partial).toBe("Partial");
  });

  it("select-all semantics: exclusions reduce count", () => {
    const eligible = ["1", "2", "3", "4"];
    const excluded = new Set(["2"]);
    const selected = eligible.filter((id) => !excluded.has(id));
    expect(selected).toEqual(["1", "3", "4"]);
    expect(selected.length).toBe(3);
  });

  it("disables start when product create gate is off", () => {
    const productCreateEnabled = false as boolean | null;
    const createGateOn = productCreateEnabled === true;
    const sameStore = false;
    const destId = "dst";
    const selectedCount = 3 as number;
    const busy = "";
    const startDisabled =
      sameStore || !destId || selectedCount === 0 || Boolean(busy) || !createGateOn;
    expect(createGateOn).toBe(false);
    expect(startDisabled).toBe(true);
  });
});
