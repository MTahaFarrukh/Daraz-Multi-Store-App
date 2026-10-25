import { describe, expect, it } from "vitest";
import {
  apiHealth,
  statusLabel,
  statusTone,
  storeStatus,
  tokenStatus,
} from "@/lib/storeHealth";
import type { StoreView } from "@/types/api";

function store(partial: Partial<StoreView>): StoreView {
  return {
    connected: true,
    store_id: "s1",
    display_name: "Demo",
    store_name: "Demo",
    account: "a@x.com",
    seller_id: "1",
    country: "pk",
    ...partial,
  };
}

describe("storeHealth labeling", () => {
  it("does not call token-OK stores Healthy / API verified", () => {
    const s = store({
      connection_status: "connected",
      needs_attention: false,
      token_status: "ok",
      api_health: "unverified",
      access_token_expires_in_seconds: 40 * 86400,
    });
    expect(storeStatus(s)).toBe("connected");
    expect(tokenStatus(s)).toBe("ok");
    expect(apiHealth(s)).toBe("unverified");
    expect(statusLabel(s)).toBe("Token OK (unverified)");
    expect(statusTone(s)).toBe("muted");
    expect(statusLabel(s).toLowerCase()).not.toContain("healthy");
  });

  it("labels verified API separately from token expiry", () => {
    const s = store({
      connection_status: "connected",
      needs_attention: false,
      token_status: "ok",
      api_health: "ok",
    });
    expect(statusLabel(s)).toBe("API verified");
    expect(statusTone(s)).toBe("ok");
  });

  it("marks API failure as danger even when token looks fine", () => {
    const s = store({
      connection_status: "connected",
      needs_attention: false,
      token_status: "ok",
      api_health: "error",
    });
    expect(statusLabel(s)).toBe("API check failed");
    expect(statusTone(s)).toBe("danger");
  });
});
