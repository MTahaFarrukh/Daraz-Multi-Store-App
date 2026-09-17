import { describe, expect, it } from "vitest";
import {
  countVisiblePrintState,
  isUnprintedOrder,
  partitionPrintSelection,
  resolvePrintLabelStatus,
  selectAllIds,
  selectUnprintedIds,
} from "@/lib/printLabelStatus";

const unprinted = {
  id: "a",
  status_group: "ready_to_ship",
  has_print: false,
  print_count: 0,
};
const printed = {
  id: "b",
  status_group: "ready_to_ship",
  has_print: true,
  print_count: 1,
  last_printed_at: "2026-09-15T05:02:00+00:00",
};
const reprinted = {
  id: "c",
  status_group: "ready_to_ship",
  has_print: true,
  print_count: 3,
  last_printed_at: "2026-09-15T09:15:00+00:00",
};

describe("print label status from events only", () => {
  it("marks UNPRINTED when no print events", () => {
    const info = resolvePrintLabelStatus(unprinted);
    expect(info.kind).toBe("unprinted");
    expect(info.text).toBe("UNPRINTED");
  });

  it("marks Printed / Reprinted from print_count, not order timestamps", () => {
    expect(resolvePrintLabelStatus(printed).kind).toBe("printed");
    expect(resolvePrintLabelStatus(reprinted).kind).toBe("reprinted");
    expect(resolvePrintLabelStatus(reprinted).text).toContain("Reprinted 3×");
    // Newer order without events stays unprinted even if "after" a print time
    const laterUnprinted = {
      id: "d",
      status_group: "ready_to_ship",
      has_print: false,
      print_count: 0,
      created_at_daraz: "2026-09-15T12:00:00+00:00",
    };
    expect(isUnprintedOrder(laterUnprinted)).toBe(true);
  });
});

describe("RTS selection helpers", () => {
  const visible = [unprinted, printed, reprinted];

  it("Select All selects printed + unprinted visible RTS", () => {
    expect(selectAllIds(visible)).toEqual(["a", "b", "c"]);
  });

  it("Select Unprinted selects only targets without print events", () => {
    expect(selectUnprintedIds(visible)).toEqual(["a"]);
  });

  it("Clear Selection is empty set (empty != all)", () => {
    expect(selectAllIds([])).toEqual([]);
    expect(partitionPrintSelection(visible, []).selected).toEqual([]);
  });

  it("counts visible print state", () => {
    expect(countVisiblePrintState(visible)).toEqual({ unprinted: 1, printed: 2 });
  });

  it("partitions mixed selection for HITL", () => {
    const part = partitionPrintSelection(visible, ["a", "b"]);
    expect(part.unprinted.map((o) => o.id)).toEqual(["a"]);
    expect(part.printed.map((o) => o.id)).toEqual(["b"]);
  });
});

describe("HITL 28/21/7 Select All must not drop unprinted", () => {
  /** Live bug: 28 RTS, 21 printed, 7 unprinted (possibly unhydrated) — Select All. */
  const rts28 = Array.from({ length: 28 }, (_, i) => {
    const isPrinted = i < 21;
    return {
      id: `ord-${i + 1}`,
      status_group: "ready_to_ship" as const,
      has_print: isPrinted,
      print_count: isPrinted ? 1 : 0,
      // Unprinted may lack local items; print status is still event-based.
      items_hydrated: isPrinted,
    };
  });

  it("Select All partitions 28 selected → Print 7 Unprinted / Reprint All 28", () => {
    const allIds = selectAllIds(rts28);
    expect(allIds).toHaveLength(28);
    const part = partitionPrintSelection(rts28, allIds);
    expect(part.selected).toHaveLength(28);
    expect(part.printed).toHaveLength(21);
    expect(part.unprinted).toHaveLength(7);
    // HITL button labels
    expect(`Print ${part.unprinted.length} Unprinted`).toBe("Print 7 Unprinted");
    expect(`Reprint All ${part.selected.length}`).toBe("Reprint All 28");
    // Assert selected IDs match visible order ids
    expect(part.selected.map((o) => o.id).sort()).toEqual([...allIds].sort());
  });

  it("Select Unprinted selects exactly the 7 (print must not include the 21)", () => {
    const unprintedIds = selectUnprintedIds(rts28);
    expect(unprintedIds).toEqual([
      "ord-22",
      "ord-23",
      "ord-24",
      "ord-25",
      "ord-26",
      "ord-27",
      "ord-28",
    ]);
    const part = partitionPrintSelection(rts28, unprintedIds);
    expect(part.printed).toHaveLength(0);
    expect(part.unprinted.map((o) => o.id)).toEqual(unprintedIds);
  });
});
