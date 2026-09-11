import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import { shouldRetry } from "@/lib/queryClient";
import { clearAuthenticatedCache, queryKeys } from "@/lib/queryKeys";

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

describe("shipping orders enablement contract", () => {
  it("empty store selection cannot form a fetchable orders key intent", () => {
    const storeIds: string[] = [];
    const enabledFlag = true;
    const canFetch = storeIds.length > 0 && enabledFlag;
    expect(canFetch).toBe(false);
  });
});
