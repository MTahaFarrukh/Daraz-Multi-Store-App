import { api, downloadAuthenticated } from "@/lib/api/client";
import type {
  BootstrapResponse,
  MeResponse,
  OrderDetailResponse,
  OrderListParams,
  OrderListResponse,
  OrderRow,
  OrderStatusCountsResponse,
  OrderSyncResponse,
  PerformanceLeaderboardResponse,
  PerformanceMonthsResponse,
  PerformanceSyncResponse,
  PrintJobListItem,
  PrintJobStatus,
  PrintOrdersStartResponse,
  PrintValidateResponse,
  ProductCloneDraftResponse,
  ProductDefaults,
  ProductDetailResponse,
  ProductListParams,
  ProductListResponse,
  ProductSyncResponse,
  StoreGroup,
  StoreView,
  UnifiedOrder,
} from "@/types/api";

function buildOrdersQs(params: OrderListParams): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value == null || value === "") continue;
    qs.set(key, String(value));
  }
  return qs.toString();
}

export const Api = {
  me: () => api<MeResponse>("/api/me"),
  bootstrap: () => api<BootstrapResponse>("/api/bootstrap", { method: "POST" }),

  listStores: () => api<{ stores: StoreView[]; workspace_id: string }>("/api/stores"),
  renameStore: (storeId: string, display_name: string) =>
    api<{ store: StoreView }>(`/api/stores/${encodeURIComponent(storeId)}`, {
      method: "PATCH",
      body: JSON.stringify({ display_name }),
    }),
  refreshTokens: (storeIds: string[], force = true) => {
    const qs = new URLSearchParams({ stores: storeIds.join(","), force: String(force) });
    return api<{ results: Array<{ store_id: string; status: string; error?: string }> }>(
      `/api/refresh-tokens?${qs}`,
      { method: "POST" }
    );
  },
  oauthStart: () => api<{ authorize_url: string }>("/api/oauth/start"),

  listGroups: () => api<{ groups: StoreGroup[] }>("/api/store-groups"),
  createGroup: (name: string, store_ids: string[]) =>
    api<{ group: StoreGroup }>("/api/store-groups", {
      method: "POST",
      body: JSON.stringify({ name, store_ids }),
    }),
  updateGroup: (groupId: string, body: { name?: string; store_ids?: string[] }) =>
    api<{ group: StoreGroup }>(`/api/store-groups/${encodeURIComponent(groupId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteGroup: (groupId: string) =>
    api<{ ok: boolean }>(`/api/store-groups/${encodeURIComponent(groupId)}`, {
      method: "DELETE",
    }),
  importBrowserProfiles: (profiles: Record<string, string[]>) =>
    api<{ imported: StoreGroup[]; skipped: string[] }>("/api/store-groups/import-browser", {
      method: "POST",
      body: JSON.stringify({ profiles }),
    }),

  /** Live Daraz RTS fetch — used by Shipping. Empty stores ≠ all. */
  listOrders: (storeIds: string[], limit: number, status = "ready_to_ship") => {
    const qs = new URLSearchParams({
      stores: storeIds.join(","),
      limit: String(limit),
      status,
    });
    return api<{ orders: OrderRow[]; count: number }>(`/api/orders/live?${qs}`);
  },

  /** Local DB unified orders (does not call Daraz). Omit stores = all workspace stores. */
  listUnifiedOrders: (params: OrderListParams = {}) => {
    const qs = buildOrdersQs(params);
    return api<OrderListResponse>(qs ? `/api/orders?${qs}` : "/api/orders");
  },

  orderStatusCounts: (params: { stores?: string; store_ids?: string } = {}) => {
    const qs = buildOrdersQs(params);
    return api<OrderStatusCountsResponse>(
      qs ? `/api/orders/status-counts?${qs}` : "/api/orders/status-counts"
    );
  },

  getOrderDetail: (orderId: string) =>
    api<OrderDetailResponse>(`/api/orders/${encodeURIComponent(orderId)}`),

  syncOrders: (body?: { store_ids?: string[]; days?: number }) =>
    api<OrderSyncResponse>("/api/orders/sync", {
      method: "POST",
      body: JSON.stringify(body || {}),
    }),

  /** Live current RTS for Shipping — does not require prior warehouse sync. */
  loadShippingRts: (store_ids: string[], upsert_headers = true) =>
    api<{
      source: string;
      partial: boolean;
      stores_requested: number;
      stores_ok: number;
      stores_failed: number;
      orders: UnifiedOrder[];
      count: number;
      unprinted_count: number;
      elapsed_ms: number;
      concurrency: number;
      stores: Array<{
        store_id?: string;
        display_name?: string;
        ok?: boolean;
        incomplete?: boolean;
        error?: string | null;
        unique?: number;
        countTotal?: number | null;
        elapsed_ms?: number;
      }>;
    }>("/api/shipping/rts", {
      method: "POST",
      body: JSON.stringify({ store_ids, upsert_headers }),
    }),

  validatePrintLabels: (order_ids: string[]) =>
    api<PrintValidateResponse>("/api/print-labels/validate", {
      method: "POST",
      body: JSON.stringify({ order_ids }),
    }),

  printOrdersByIds: (order_ids: string[], allow_reprint = false) =>
    api<PrintOrdersStartResponse>("/api/print-labels/orders", {
      method: "POST",
      body: JSON.stringify({ order_ids, allow_reprint }),
    }),

  /** Legacy live print by store selection. Duplicate protection: allow_reprint=false by default. */
  startPrint: (storeIds: string[], limit: number, allowReprint = false) => {
    const qs = new URLSearchParams({
      stores: storeIds.join(","),
      limit: String(limit),
      status: "ready_to_ship",
      allow_reprint: String(allowReprint),
    });
    return api<{
      status: string;
      job_id: string;
      poll_url: string;
      download_url: string;
      message?: string;
    }>(`/api/print-labels?${qs}`, { method: "POST" });
  },
  printStatus: (jobId: string) =>
    api<PrintJobStatus>(`/api/print-labels/${encodeURIComponent(jobId)}/status`),
  listPrintJobs: () => api<{ jobs: PrintJobListItem[] }>("/api/print-jobs"),
  downloadPrint: (jobId: string) =>
    downloadAuthenticated(
      `/api/print-labels/${encodeURIComponent(jobId)}/download`,
      "combined-labels.pdf"
    ),

  performanceMonths: () => api<PerformanceMonthsResponse>("/api/store-performance/months"),
  storePerformance: (year: number, month: number, metric: "orders" | "gross_sales" = "orders") => {
    const qs = new URLSearchParams({
      year: String(year),
      month: String(month),
      metric,
    });
    return api<PerformanceLeaderboardResponse>(`/api/store-performance?${qs}`);
  },
  syncStorePerformance: (year: number, month: number, includeGrossSales = true) => {
    const qs = new URLSearchParams({
      year: String(year),
      month: String(month),
      include_gross_sales: String(includeGrossSales),
    });
    return api<PerformanceSyncResponse>(`/api/store-performance/sync?${qs}`, { method: "POST" });
  },

  listProducts: (params: ProductListParams = {}) => {
    const qs = buildOrdersQs(params as OrderListParams);
    return api<ProductListResponse>(qs ? `/api/products?${qs}` : "/api/products");
  },

  getProduct: (productId: string) =>
    api<ProductDetailResponse>(`/api/products/${encodeURIComponent(productId)}`),

  syncProducts: (body?: { store_ids?: string[]; fetch_details?: boolean }) =>
    api<ProductSyncResponse>("/api/products/sync", {
      method: "POST",
      body: JSON.stringify(body || {}),
    }),

  getProductDefaults: () => api<{ defaults: ProductDefaults }>("/api/product-defaults"),

  saveProductDefaults: (body: Partial<ProductDefaults>) =>
    api<{ defaults: ProductDefaults }>("/api/product-defaults", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  prepareCloneDraft: (productId: string, destination_store_id: string) =>
    api<ProductCloneDraftResponse>(
      `/api/products/${encodeURIComponent(productId)}/clone-draft`,
      {
        method: "POST",
        body: JSON.stringify({ destination_store_id }),
      }
    ),
};
