import { describe, expect, it } from "vitest";
import { formatAuditAction } from "@/lib/capabilities";
import { destinationProgressLabel } from "@/lib/productAddProgress";
import { printJobStageLabel } from "@/lib/printJobStatus";
import { queryKeys } from "@/lib/queryKeys";
import { FINANCE_EMPTY_MESSAGE, formatFinanceAmount } from "@/lib/financeUi";
import { INVENTORY_EMPTY_MESSAGE, formatStockQuantity } from "@/lib/inventoryLabels";
import { formatMomPct } from "@/lib/analyticsFormat";

describe("Batch 6 UI humanization", () => {
  it("humanizes audit actions", () => {
    expect(formatAuditAction("product.reconcile.verified")).toBe(
      "Product reconciliation verified"
    );
    expect(formatAuditAction("finance.sync.completed")).toMatch(/Finance sync/i);
  });

  it("humanizes product add + print stages", () => {
    expect(
      destinationProgressLabel({ attempt_state: "NEEDS_RECONCILIATION" })
    ).toBe("Needs reconciliation");
    expect(destinationProgressLabel({ attempt_state: "ANALYZING" })).toBe("Analyzing");
    expect(printJobStageLabel({ processing_stage: "MERGING_PDF" })).toBe("Merging PDF");
    expect(printJobStageLabel({ processing_stage: "SAVING_HISTORY" })).toBe(
      "Saving history"
    );
  });

  it("keeps empty/error helpers stable", () => {
    expect(INVENTORY_EMPTY_MESSAGE).toMatch(/Sync Products/);
    expect(FINANCE_EMPTY_MESSAGE).toMatch(/not been synced/);
    expect(formatStockQuantity(null)).toBe("Unknown");
    expect(formatFinanceAmount(null)).toBe("—");
    expect(formatMomPct(null)).toBe("N/A");
  });

  it("query keys isolate workspace + filters", () => {
    expect(queryKeys.inventory("ws-a", "q=1")).not.toEqual(
      queryKeys.inventory("ws-b", "q=1")
    );
    expect(queryKeys.analytics("ws", 2026, 3, "s1")).toEqual([
      "workspace",
      "ws",
      "analytics",
      2026,
      3,
      "s1",
    ]);
    expect(queryKeys.financeSummary("ws", "store-a")[3]).toBe("store-a");
  });
});
