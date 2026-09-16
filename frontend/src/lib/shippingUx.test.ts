import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const shippingPage = readFileSync(
  resolve(__dirname, "../pages/ShippingPage.tsx"),
  "utf8"
);
const apiIndex = readFileSync(resolve(__dirname, "api/index.ts"), "utf8");

describe("Shipping UX — Load RTS only", () => {
  it("exposes Load RTS as the sole Shipping data-retrieval button label", () => {
    expect(shippingPage).toMatch(/>\s*Load RTS\s*</);
    expect(shippingPage).toContain('data-testid="shipping-load-rts"');
    // No Sync / Sync Orders / Sync + Load / Load Orders on Shipping
    expect(shippingPage).not.toMatch(/>\s*Sync(\s+Orders)?\s*</);
    expect(shippingPage).not.toContain("Sync + Load");
    expect(shippingPage).not.toContain("Load Orders");
  });

  it("loads via Api.loadShippingRts, not Api.syncOrders", () => {
    expect(shippingPage).toContain("Api.loadShippingRts");
    expect(shippingPage).not.toContain("syncOrders");
    expect(shippingPage).not.toContain("/api/orders/sync");
    expect(apiIndex).toContain('"/api/shipping/rts"');
    // Backend order sync remains available for Orders page
    expect(apiIndex).toContain('"/api/orders/sync"');
  });

  it("documents the seller workflow without Sync choice", () => {
    expect(shippingPage).toContain(
      "Select stores → Load RTS → Select Unprinted → Print Labels"
    );
  });
});
