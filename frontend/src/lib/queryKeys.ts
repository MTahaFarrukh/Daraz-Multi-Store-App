/**
 * Centralized TanStack Query key factory.
 *
 * Tenant-scoped resources ALWAYS include workspaceId.
 * Future community resources must use ["community", communityId, ...] — never reuse workspace keys.
 */

export type PerformanceMetric = "orders" | "gross_sales";

export const queryKeys = {
  /** Auth profile — optional cache; auth session remains in useAuth/Supabase. */
  me: ["me"] as const,

  workspace: (workspaceId: string) => ["workspace", workspaceId] as const,

  stores: (workspaceId: string) => ["workspace", workspaceId, "stores"] as const,

  storeGroups: (workspaceId: string) => ["workspace", workspaceId, "store-groups"] as const,

  performanceMonths: (workspaceId: string) =>
    ["workspace", workspaceId, "store-performance", "months"] as const,

  performance: (
    workspaceId: string,
    year: number,
    month: number,
    metric: PerformanceMetric
  ) => ["workspace", workspaceId, "store-performance", year, month, metric] as const,

  /** Prefix for invalidating all performance boards for a workspace period. */
  performancePeriod: (workspaceId: string, year: number, month: number) =>
    ["workspace", workspaceId, "store-performance", year, month] as const,

  printJobs: (workspaceId: string) => ["workspace", workspaceId, "print-jobs"] as const,

  /**
   * RTS orders — only fetched when explicitly enabled (user clicked Load Orders).
   * Empty storeIds must NOT enable the query.
   */
  orders: (workspaceId: string, storeIds: string[], limit: number, status = "ready_to_ship") =>
    [
      "workspace",
      workspaceId,
      "orders",
      status,
      limit,
      [...storeIds].sort().join(","),
    ] as const,

  // Future Phase 3+ (not implemented):
  // products: (workspaceId: string) => ["workspace", workspaceId, "products"] as const,
  // inventory: (workspaceId: string) => ["workspace", workspaceId, "inventory"] as const,
  // finance: (workspaceId: string) => ["workspace", workspaceId, "finance"] as const,
  // communityPerformance: (communityId: string, year: number, month: number) =>
  //   ["community", communityId, "performance", year, month] as const,
} as const;

/** Clear all authenticated/workspace caches (logout / identity change). */
export function clearAuthenticatedCache(client: {
  clear: () => void;
}): void {
  client.clear();
}
