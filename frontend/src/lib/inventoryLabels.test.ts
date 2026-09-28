import { describe, expect, it } from "vitest";
import {
  INVENTORY_EMPTY_MESSAGE,
  formatStockQuantity,
  stockBadgeForQuantity,
  stockBadgeTone,
} from "./inventoryLabels";

describe("inventory stock labels", () => {
  it("treats null/undefined as Unknown, not zero", () => {
    expect(stockBadgeForQuantity(null)).toBe("Unknown");
    expect(stockBadgeForQuantity(undefined)).toBe("Unknown");
    expect(formatStockQuantity(null)).toBe("Unknown");
    expect(formatStockQuantity(0)).toBe("0");
    expect(stockBadgeForQuantity(0)).toBe("Out of Stock");
  });

  it("classifies low / in stock with threshold", () => {
    expect(stockBadgeForQuantity(3, 5)).toBe("Low Stock");
    expect(stockBadgeForQuantity(5, 5)).toBe("Low Stock");
    expect(stockBadgeForQuantity(6, 5)).toBe("In Stock");
    expect(stockBadgeTone("Low Stock")).toBe("warn");
    expect(stockBadgeTone("Unknown")).toBe("muted");
  });

  it("exposes empty-state copy", () => {
    expect(INVENTORY_EMPTY_MESSAGE).toMatch(/Sync Products/);
  });
});
