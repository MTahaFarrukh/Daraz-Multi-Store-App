import { describe, expect, it } from "vitest";
import {
  brandCheckBlocksCreate,
  canCheckNoBrand,
  canContinueDeepScan,
  extractPreviewPrimaryCategoryId,
  formatCatalogEvidenceLines,
  formatNoBrandCheckLine,
  isNoBrandConfirmed,
  mapApiNoBrandResult,
  noBrandCheckFromHttpError,
  noBrandCheckTone,
  type NoBrandCheckResult,
} from "@/lib/noBrandCheck";

describe("noBrandCheck helpers", () => {
  it("disables Check No Brand without category id (never guesses)", () => {
    expect(canCheckNoBrand(null)).toBe(false);
    expect(canCheckNoBrand("")).toBe(false);
    expect(
      extractPreviewPrimaryCategoryId({
        draft: { product: { primary_category_id: 10002730 } },
      })
    ).toBe("10002730");
  });

  it("shows green only for NO_BRAND and includes lookup diagnostics", () => {
    const ok: NoBrandCheckResult = {
      status: "NO_BRAND",
      reason: "confirmed_no_brand_row",
      brand: "No Brand",
      brand_id: 60147,
      lookup_calls: 2,
      lookup: {
        pages: 3,
        stop_reason: "found",
        strategy: "name_filter",
        matched_start_row: 0,
        name_filter_effective: true,
        total_record: 10,
      },
    };
    expect(isNoBrandConfirmed(ok)).toBe(true);
    expect(noBrandCheckTone(ok.status)).toBe("ok");
    const line = formatNoBrandCheckLine(ok);
    expect(line).toContain("stop_reason=found");
    expect(line).toContain("pages=3");
    expect(brandCheckBlocksCreate({ s1: ok }, ["s1"])).toBe(false);
  });

  it("shows warning for UNVERIFIED and keeps create blocked", () => {
    const bad: NoBrandCheckResult = {
      status: "UNVERIFIED",
      reason: "page_limit",
      lookup: { pages: 48, stop_reason: "page_limit", total_record: 78000 },
      next_start_row: 9600,
      complete: false,
    };
    expect(isNoBrandConfirmed(bad)).toBe(false);
    expect(noBrandCheckTone(bad.status)).toBe("warn");
    expect(formatNoBrandCheckLine(bad)).toContain("stop_reason=page_limit");
    expect(brandCheckBlocksCreate({ s1: bad }, ["s1"])).toBe(true);
    expect(canContinueDeepScan(bad)).toBe(true);
    expect(
      canContinueDeepScan({
        ...bad,
        status: "NO_BRAND",
        brand: "No Brand",
      })
    ).toBe(false);
  });

  it("formats catalog evidence for diagnostics copy", () => {
    const lines = formatCatalogEvidenceLines({
      status: "OK",
      message: "Local synced catalog",
      workspace: {
        product_count: 12,
        category_product_count: 4,
        top_brands_in_category: [
          { brand: "Acme", count: 3 },
          { brand: "No Brand", count: 1 },
        ],
        no_brand_sample_item_ids: ["111", "222"],
      },
      destination_store: {
        product_count: 5,
        category_product_count: 2,
        top_brands_in_category: [{ brand: "Acme", count: 2 }],
        no_brand_sample_item_ids: [],
      },
    });
    expect(lines.some((l) => l.includes("catalog_evidence.status=OK"))).toBe(true);
    expect(lines.some((l) => l.includes("Acme=3"))).toBe(true);
    expect(lines.some((l) => l.includes("111,222"))).toBe(true);
  });

  it("maps 403 without inventing brand ids", () => {
    const err = Object.assign(new Error("Insufficient workspace capability"), {
      status: 403,
    });
    const out = noBrandCheckFromHttpError(err, "store-a", "10002730");
    expect(out.status).toBe("UNVERIFIED");
    expect(out.brand_id).toBeNull();
    expect(out.error).toMatch(/403/);
  });

  it("maps API lookup + catalog_evidence", () => {
    const mapped = mapApiNoBrandResult(
      {
        status: "UNVERIFIED",
        reason: "page_limit",
        lookup: {
          pages: 48,
          stop_reason: "page_limit",
          ordering: "unreliable_in_window",
          matched_start_row: null,
          total_record: 78000,
        },
        catalog_evidence: { status: "NOT_SYNCED", message: "sync first" },
      },
      "s1",
      "10002730"
    );
    expect(mapped.lookup?.stop_reason).toBe("page_limit");
    expect(mapped.lookup?.ordering).toBe("unreliable_in_window");
    expect(mapped.catalog_evidence?.status).toBe("NOT_SYNCED");
  });
});
