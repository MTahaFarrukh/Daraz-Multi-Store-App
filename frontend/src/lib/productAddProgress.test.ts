import { describe, expect, it } from "vitest";
import {
  buildAttentionSummary,
  buildClientTimeline,
  destinationActionKind,
  destinationProgressLabel,
  destinationReasonLabel,
  destinationRecoveryHint,
  destinationUiStatus,
  extractDiagnosticId,
  formatDiagnosticsCopy,
  shouldShowReconcile,
  shouldShowRetry,
} from "@/lib/productAddProgress";

describe("productAddProgress", () => {
  it("maps attempt states to progress labels", () => {
    expect(destinationProgressLabel({ status: "CREATING" })).toBe("Creating");
    expect(destinationProgressLabel({ attempt_state: "CREATED_UNVERIFIED" })).toBe(
      "Verifying"
    );
    expect(destinationProgressLabel({ creation_status: "VERIFIED" })).toBe("Succeeded");
    expect(
      destinationProgressLabel({ creation_status: "NEEDS_RECONCILIATION" })
    ).toBe("Needs attention");
    expect(
      destinationProgressLabel({ creation_status: "FAILED_SAFE_TO_RETRY" })
    ).toBe("Failed");
    expect(destinationProgressLabel({ status: "NEEDS_ATTENTION" })).toBe(
      "Needs attention"
    );
    expect(destinationProgressLabel({ status: "ALREADY_EXISTS" })).toBe(
      "Already on destination"
    );
  });

  it("maps internal reasons to actionable labels", () => {
    expect(destinationReasonLabel("already_copied_to_destination")).toMatch(
      /Already copied/
    );
    expect(destinationReasonLabel("concurrent_create_claim_held")).toMatch(
      /in progress/
    );
    expect(destinationReasonLabel("CreateProduct:E123:boom")).toMatch(/rejected/);
  });

  it("shows Retry only for FAILED_SAFE_TO_RETRY with attempt_id", () => {
    expect(
      shouldShowRetry({
        creation_status: "FAILED_SAFE_TO_RETRY",
        attempt_id: "a1",
      })
    ).toBe(true);
    expect(
      shouldShowRetry({
        creation_status: "FAILED_SAFE_TO_RETRY",
      })
    ).toBe(false);
    expect(
      shouldShowRetry({
        creation_status: "VERIFIED",
        attempt_id: "a1",
      })
    ).toBe(false);
  });

  it("shows Reconcile only for NEEDS_RECONCILIATION", () => {
    expect(
      shouldShowReconcile({
        creation_status: "NEEDS_RECONCILIATION",
        attempt_id: "a2",
      })
    ).toBe(true);
    expect(
      shouldShowReconcile({
        creation_status: "FAILED_SAFE_TO_RETRY",
        attempt_id: "a2",
      })
    ).toBe(false);
    expect(destinationActionKind({ creation_status: "VERIFIED" })).toBe("none");
    expect(
      destinationActionKind({
        creation_status: "NEEDS_RECONCILIATION",
        attempt_id: "x",
      })
    ).toBe("reconcile");
    expect(
      destinationActionKind({
        creation_status: "FAILED_SAFE_TO_RETRY",
        attempt_id: "x",
      })
    ).toBe("retry");
  });

  it("builds per-store attention summary instead of a bare count", () => {
    const summary = buildAttentionSummary(
      [
        {
          store: { store_id: "mtf", display_name: "MTF Digital Emporium" },
          status: "NEEDS_ATTENTION",
          reason: "missing_price",
        },
      ],
      { diagnosticId: "cid-1" }
    );
    expect(summary.headline).toMatch(/MTF Digital Emporium/);
    expect(summary.headline).toMatch(/Enter prices/i);
    expect(summary.lines[0].recovery).toMatch(/Enter a regular price/i);
  });

  it("uses Reason unavailable when no reason is provided", () => {
    const summary = buildAttentionSummary(
      [
        {
          store: { display_name: "Store A" },
          status: "NEEDS_ATTENTION",
        },
      ],
      { diagnosticId: "abc-123" }
    );
    expect(summary.lines[0].reasonLabel).toMatch(/Reason unavailable/);
    expect(summary.lines[0].reasonLabel).toMatch(/abc-123/);
  });

  it("does not mark create as started before execute", () => {
    const idle = buildClientTimeline({
      phase: "idle",
      hasResult: false,
      executedCreate: false,
    });
    expect(idle.find((s) => s.id === "create")?.state).toBe("pending");

    const preparing = buildClientTimeline({
      phase: "preparing",
      hasResult: false,
      executedCreate: false,
    });
    expect(preparing.find((s) => s.id === "create")?.state).toBe("skipped");

    const creating = buildClientTimeline({
      phase: "creating",
      hasResult: false,
      executedCreate: true,
    });
    expect(creating.find((s) => s.id === "create")?.state).toBe("active");
  });

  it("formats redacted diagnostics copy with correlation id", () => {
    const text = formatDiagnosticsCopy({
      correlationId: "cid-xyz",
      phase: "done",
      message: "1 store needs attention",
      destinations: [
        {
          store: { display_name: "MTF" },
          status: "NEEDS_ATTENTION",
          reason: "missing_price",
          attempt_id: "att-1",
        },
      ],
      stageHistory: [{ stage: "gate_checked", at_ms: 12, gate_on: true }],
    });
    expect(text).toContain("cid-xyz");
    expect(text).toContain("MTF");
    expect(text).toContain("No tokens");
    expect(text).not.toMatch(/bearer |password=/i);
  });

  it("extracts diagnostic id from FastAPI error payloads", () => {
    expect(
      extractDiagnosticId({
        detail: { detail: { message: "boom", diagnostic_id: "d-9" } },
      })
    ).toBe("d-9");
    expect(
      extractDiagnosticId({
        detail: { message: "boom", diagnostic_id: "d-8" },
      })
    ).toBe("d-8");
  });

  it("maps ui status and recovery for attention states", () => {
    expect(
      destinationUiStatus({ status: "NEEDS_ATTENTION", reason: "missing_price" })
    ).toBe("attention");
    expect(
      destinationRecoveryHint({
        status: "NEEDS_RECONCILIATION",
        reason: "product_create_attempt_requires_reconciliation",
      })
    ).toMatch(/Reconcile/i);
  });
});
