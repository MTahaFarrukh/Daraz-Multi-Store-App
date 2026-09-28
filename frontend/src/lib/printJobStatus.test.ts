import { describe, expect, it } from "vitest";
import {
  isPrintJobInterrupted,
  printJobProgressText,
  printJobStageLabel,
  retryablePrintOrderIds,
} from "@/lib/printJobStatus";

describe("print job interrupted / recovery helpers", () => {
  it("detects interrupted status and RECOVERY_REQUIRED stage", () => {
    expect(isPrintJobInterrupted({ status: "interrupted" })).toBe(true);
    expect(
      isPrintJobInterrupted({
        status: "processing",
        processing_stage: "RECOVERY_REQUIRED",
      })
    ).toBe(true);
    expect(isPrintJobInterrupted({ status: "processing" })).toBe(false);
  });

  it("formats stage and progress", () => {
    expect(
      printJobStageLabel({
        status: "interrupted",
        processing_stage: "RECOVERY_REQUIRED",
      })
    ).toMatch(/Interrupted/i);
    expect(
      printJobProgressText({
        progress: { completed: 2, total: 5, failed: 1 },
      })
    ).toBe("2 / 5 completed · 1 failed");
  });

  it("prefers retryable_order_ids over failed_order_ids", () => {
    expect(
      retryablePrintOrderIds({
        retryable_order_ids: ["a"],
        failed_order_ids: ["a", "b"],
      })
    ).toEqual(["a"]);
  });
});
