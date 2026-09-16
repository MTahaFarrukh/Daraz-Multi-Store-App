/**
 * Centralized TanStack Query key factory.
 *
 * Tenant-scoped resources ALWAYS include workspaceId.
 * Future community resources must use ["community", communityId, ...] — never reuse workspace keys.
 */

export type PerformanceMetric = "orders" | "gross_sales";

/**
 * Stable filter key for unified orders / status-counts caches.
 * Sorts object keys and joins as `k=v` pairs (null/undefined omitted).
 */
export function normalizeOrdersFilterKey(
  filters: Record<string, string | number | null | undefined>
): string {
  return Object.keys(filters)
    .sort()
    .filter((k) => filters[k] != null && filters[k] !== "")
    .map((k) => `${k}=${String(filters[k])}`)
    .join("&");
}

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
   * Live RTS orders (Shipping) — only fetched when explicitly enabled.
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

  /** Local DB unified orders list — keyed by normalized filter string. */
  unifiedOrders: (workspaceId: string, filterKey: string) =>
    ["workspace", workspaceId, "unified-orders", filterKey] as const,

  /** Status-group counts for filter chrome (store scope). */
  orderStatusCounts: (workspaceId: string, filterKey: string) =>
    ["workspace", workspaceId, "order-status-counts", filterKey] as const,

  orderDetail: (workspaceId: string, orderId: string) =>
    ["workspace", workspaceId, "order-detail", orderId] as const,

  products: (workspaceId: string, filterKey: string) =>
    ["workspace", workspaceId, "products", filterKey] as const,

  product: (workspaceId: string, productId: string) =>
    ["workspace", workspaceId, "product", productId] as const,

  productDefaults: (workspaceId: string) =>
    ["workspace", workspaceId, "product-defaults"] as const,

  cloneDraft: (
    workspaceId: string,
    sourceProductId: string,
    destinationStoreId: string
  ) =>
    [
      "workspace",
      workspaceId,
      "clone-draft",
      sourceProductId,
      destinationStoreId,
    ] as const,
} as const;

/** Clear all authenticated/workspace caches (logout / identity change). */
export function clearAuthenticatedCache(client: {
  clear: () => void;
}): void {
  client.clear();
}
