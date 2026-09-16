import { describe, expect, it } from "vitest";
import { normalizeOrdersFilterKey } from "@/lib/queryKeys";

describe("product query keys", () => {
  it("normalizes product filters stably", () => {
    const a = normalizeOrdersFilterKey({
      page: 1,
      search: "squishy",
      stores: "store_a",
    });
    const b = normalizeOrdersFilterKey({
      stores: "store_a",
      search: "squishy",
      page: 1,
    });
    expect(a).toBe(b);
    expect(a).toContain("search=squishy");
  });
});
