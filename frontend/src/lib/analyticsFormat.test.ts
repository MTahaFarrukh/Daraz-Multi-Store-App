import { describe, expect, it } from "vitest";
import {
  ANALYTICS_EMPTY_MESSAGE,
  formatMomPct,
  formatMoneyAmount,
} from "./analyticsFormat";

describe("analytics formatting", () => {
  it("shows N/A for missing MoM (no previous data)", () => {
    expect(formatMomPct(null)).toBe("N/A");
    expect(formatMomPct(undefined)).toBe("N/A");
    expect(formatMomPct(12.5)).toBe("+12.5%");
    expect(formatMomPct(-4)).toBe("-4.0%");
  });

  it("does not invent money for null", () => {
    expect(formatMoneyAmount(null)).toBe("—");
    expect(formatMoneyAmount(1500)).toContain("1,500");
  });

  it("empty month copy", () => {
    expect(ANALYTICS_EMPTY_MESSAGE).toMatch(/No order data/);
  });
});
