import { describe, expect, it } from "vitest";
import {
  destinationActionKind,
  destinationProgressLabel,
  shouldShowReconcile,
  shouldShowRetry,
} from "@/lib/productAddProgress";

describe("productAddProgress", () => {
  it("maps attempt states to progress labels", () => {
    expect(destinationProgressLabel({ status: "CREATING" })).toBe("Creating");
    expect(destinationProgressLabel({ attempt_state: "CREATED_UNVERIFIED" })).toBe(
      "Verifying"
    );
    expect(destinationProgressLabel({ creation_status: "VERIFIED" })).toBe("Verified");
    expect(
      destinationProgressLabel({ creation_status: "NEEDS_RECONCILIATION" })
    ).toBe("Needs reconciliation");
    expect(
      destinationProgressLabel({ creation_status: "FAILED_SAFE_TO_RETRY" })
    ).toBe("Failed");
    expect(destinationProgressLabel({ status: "NEEDS_ATTENTION" })).toBe(
      "Needs attention"
    );
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
});
