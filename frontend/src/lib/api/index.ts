import { api, downloadAuthenticated } from "@/lib/api/client";
import type {
  BootstrapResponse,
  MeResponse,
  OrderRow,
  PerformanceLeaderboardResponse,
  PerformanceMonthsResponse,
  PerformanceSyncResponse,
  PrintJobListItem,
  PrintJobStatus,
  StoreGroup,
  StoreView,
} from "@/types/api";

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

  listOrders: (storeIds: string[], limit: number, status = "ready_to_ship") => {
    const qs = new URLSearchParams({
      stores: storeIds.join(","),
      limit: String(limit),
      status,
    });
    return api<{ orders: OrderRow[]; count: number }>(`/api/orders?${qs}`);
  },

  startPrint: (storeIds: string[], limit: number) => {
    const qs = new URLSearchParams({
      stores: storeIds.join(","),
      limit: String(limit),
      status: "ready_to_ship",
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
};
