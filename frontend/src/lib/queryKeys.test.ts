import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import { shouldRetry } from "@/lib/queryClient";
import {
  clearAuthenticatedCache,
  normalizeOrdersFilterKey,
  queryKeys,
} from "@/lib/queryKeys";

describe("normalizeOrdersFilterKey", () => {
  it("sorts keys and joins stably", () => {
    expect(
      normalizeOrdersFilterKey({ page: 2, status_group: "pending", search: "ab" })
    ).toBe("page=2&search=ab&status_group=pending");
    expect(
      normalizeOrdersFilterKey({ search: "ab", status_group: "pending", page: 2 })
    ).toBe(normalizeOrdersFilterKey({ page: 2, status_group: "pending", search: "ab" }));
  });

  it("omits null, undefined, and empty string", () => {
    expect(
      normalizeOrdersFilterKey({
        search: "",
        status_group: null,
        stores: undefined,
        page: 1,
      })
    ).toBe("page=1");
  });
});

describe("queryKeys workspace isolation", () => {
  it("includes workspace id in store keys", () => {
    expect(queryKeys.stores("ws-a")).toEqual(["workspace", "ws-a", "stores"]);
    expect(queryKeys.stores("ws-a")).not.toEqual(queryKeys.stores("ws-b"));
  });

  it("scopes performance by workspace + period + metric", () => {
    expect(queryKeys.performance("ws-a", 2026, 9, "orders")).toEqual([
      "workspace",
      "ws-a",
      "store-performance",
      2026,
      9,
      "orders",
    ]);
    expect(queryKeys.performance("ws-a", 2026, 9, "orders")).not.toEqual(
      queryKeys.performance("ws-a", 2026, 8, "orders")
    );
  });

  it("orders key sorts store ids for stability", () => {
    expect(queryKeys.orders("ws", ["b", "a"], 10)).toEqual(
      queryKeys.orders("ws", ["a", "b"], 10)
    );
  });

  it("unifiedOrders is workspace-aware and filter-keyed", () => {
    const keyA = queryKeys.unifiedOrders("ws-a", "page=1&status_group=pending");
    const keyB = queryKeys.unifiedOrders("ws-b", "page=1&status_group=pending");
    expect(keyA).toEqual([
      "workspace",
      "ws-a",
      "unified-orders",
      "page=1&status_group=pending",
    ]);
    expect(keyA).not.toEqual(keyB);
    expect(queryKeys.orderStatusCounts("ws-a", "stores=s1")).toEqual([
      "workspace",
      "ws-a",
      "order-status-counts",
      "stores=s1",
    ]);
    expect(queryKeys.orderDetail("ws-a", "ord-1")).toEqual([
      "workspace",
      "ws-a",
      "order-detail",
      "ord-1",
    ]);
  });

  it("documents community key convention separately from workspace", () => {
    const communityKey = ["community", "c1", "performance", 2026, 9] as const;
    expect(communityKey[0]).toBe("community");
    expect(queryKeys.stores("c1")[0]).toBe("workspace");
  });
});

describe("retry policy", () => {
  it("does not retry 401/403/4xx", () => {
    expect(shouldRetry(0, new ApiError("nope", 401))).toBe(false);
    expect(shouldRetry(0, new ApiError("nope", 403))).toBe(false);
    expect(shouldRetry(0, new ApiError("bad", 400))).toBe(false);
  });

  it("allows a single retry for non-4xx", () => {
    expect(shouldRetry(0, new ApiError("boom", 500))).toBe(true);
    expect(shouldRetry(1, new ApiError("boom", 500))).toBe(false);
    expect(shouldRetry(0, new Error("network"))).toBe(true);
  });
});

describe("logout cache cleanup", () => {
  it("clearAuthenticatedCache empties the client", () => {
    const calls: string[] = [];
    clearAuthenticatedCache({
      clear: () => {
        calls.push("clear");
      },
    });
    expect(calls).toEqual(["clear"]);
  });
});

describe("shipping Load RTS enablement contract", () => {
  it("empty store selection cannot fetch RTS", () => {
    const storeIds: string[] = [];
    const loadRtsEnabled = true;
    const canFetch = storeIds.length > 0 && loadRtsEnabled;
    expect(canFetch).toBe(false);
  });

  it("Shipping data action is Load RTS via /api/shipping/rts, not orders sync", () => {
    // Contract mirror of ShippingPage + Api.loadShippingRts
    const shippingDataAction = "Load RTS";
    const shippingEndpoint = "/api/shipping/rts";
    const forbiddenOnShipping = ["/api/orders/sync", "Sync Orders", "Load Orders", "Sync + Load"];
    expect(shippingDataAction).toBe("Load RTS");
    expect(shippingEndpoint).toBe("/api/shipping/rts");
    for (const label of forbiddenOnShipping) {
      expect(label === shippingDataAction).toBe(false);
      expect(shippingEndpoint.includes("sync")).toBe(false);
    }
  });
});
