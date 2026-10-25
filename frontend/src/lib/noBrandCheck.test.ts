import { describe, expect, it } from "vitest";
import {
  brandCheckBlocksCreate,
  canCheckNoBrand,
  extractPreviewPrimaryCategoryId,
  formatNoBrandCheckLine,
  isNoBrandConfirmed,
  noBrandCheckFromHttpError,
  noBrandCheckTone,
  type NoBrandCheckResult,
} from "@/lib/noBrandCheck";

describe("noBrandCheck helpers", () => {
  it("disables Check No Brand without category id (never guesses)", () => {
    expect(canCheckNoBrand(null)).toBe(false);
    expect(canCheckNoBrand("")).toBe(false);
    expect(canCheckNoBrand("   ")).toBe(false);
    expect(extractPreviewPrimaryCategoryId(null)).toBe(null);
    expect(extractPreviewPrimaryCategoryId({ draft: { product: {} } })).toBe(null);
    expect(
      extractPreviewPrimaryCategoryId({
        draft: { product: { primary_category_id: 10002730 } },
      })
    ).toBe("10002730");
    expect(canCheckNoBrand("10002730")).toBe(true);
  });

  it("shows green only for NO_BRAND", () => {
    const ok: NoBrandCheckResult = {
      status: "NO_BRAND",
      reason: "confirmed_no_brand_row",
      brand: "No Brand",
      brand_id: 60147,
      lookup_calls: 2,
    };
    expect(isNoBrandConfirmed(ok)).toBe(true);
    expect(noBrandCheckTone(ok.status)).toBe("ok");
    expect(formatNoBrandCheckLine(ok)).toContain("status=NO_BRAND");
    expect(formatNoBrandCheckLine(ok)).toContain("brand=No Brand");
    expect(formatNoBrandCheckLine(ok)).toContain("brand_id=60147");
    expect(formatNoBrandCheckLine(ok)).toContain("lookup_calls=2");
    expect(brandCheckBlocksCreate({ s1: ok }, ["s1"])).toBe(false);
  });

  it("shows warning for UNVERIFIED and blocks create", () => {
    const bad: NoBrandCheckResult = {
      status: "UNVERIFIED",
      reason: "no_brand_scan_incomplete",
      brand: null,
      brand_id: null,
      lookup_calls: 3,
    };
    expect(isNoBrandConfirmed(bad)).toBe(false);
    expect(noBrandCheckTone(bad.status)).toBe("warn");
    expect(formatNoBrandCheckLine(bad)).toContain("status=UNVERIFIED");
    expect(formatNoBrandCheckLine(bad)).not.toMatch(/brand_id=\d/);
    expect(brandCheckBlocksCreate({ s1: bad }, ["s1"])).toBe(true);
  });

  it("maps 403 to UNVERIFIED permission message without inventing brand ids", () => {
    const err = Object.assign(new Error("Insufficient workspace capability"), {
      status: 403,
    });
    const out = noBrandCheckFromHttpError(err, "store-a", "10002730");
    expect(out.status).toBe("UNVERIFIED");
    expect(out.reason).toBe("permission_denied");
    expect(out.http_status).toBe(403);
    expect(out.brand).toBeNull();
    expect(out.brand_id).toBeNull();
    expect(out.error).toMatch(/403/);
    expect(out.error).toMatch(/product\.create/);
    expect(brandCheckBlocksCreate({ "store-a": out }, ["store-a"])).toBe(true);
  });
});
