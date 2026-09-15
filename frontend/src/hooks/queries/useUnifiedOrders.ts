import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { normalizeOrdersFilterKey, queryKeys } from "@/lib/queryKeys";
import type { OrderListParams, PrintStateFilter } from "@/types/api";

const UNIFIED_ORDERS_STALE_MS = 30_000;
const STATUS_COUNTS_STALE_MS = 30_000;

export type UnifiedOrdersFilters = {
  stores?: string;
  store_ids?: string;
  group_id?: string;
  status_group?: string;
  search?: string;
  date_from?: string;
  date_to?: string;
  print_state?: PrintStateFilter;
  page?: number;
  page_size?: number;
  sort?: string;
};

function toListParams(filters: UnifiedOrdersFilters): OrderListParams {
  return {
    stores: filters.stores,
    store_ids: filters.store_ids,
    group_id: filters.group_id,
    status_group: filters.status_group,
    search: filters.search,
    date_from: filters.date_from,
    date_to: filters.date_to,
    print_state: filters.print_state,
    page: filters.page,
    page_size: filters.page_size,
    sort: filters.sort,
  };
}

export function useUnifiedOrders(
  workspaceId: string | undefined,
  filters: UnifiedOrdersFilters,
  options?: { enabled?: boolean }
) {
  const params = toListParams(filters);
  const filterKey = normalizeOrdersFilterKey(
    params as Record<string, string | number | null | undefined>
  );
  const enabled =
    Boolean(workspaceId) && (options?.enabled !== undefined ? options.enabled : true);
  return useQuery({
    queryKey: queryKeys.unifiedOrders(workspaceId || "__none__", filterKey),
    queryFn: () => Api.listUnifiedOrders(params),
    enabled,
    staleTime: UNIFIED_ORDERS_STALE_MS,
    placeholderData: (prev) => prev,
  });
}

export function useOrderStatusCounts(
  workspaceId: string | undefined,
  storeFilter: { stores?: string; store_ids?: string } = {}
) {
  const filterKey = normalizeOrdersFilterKey(storeFilter);
  return useQuery({
    queryKey: queryKeys.orderStatusCounts(workspaceId || "__none__", filterKey),
    queryFn: () => Api.orderStatusCounts(storeFilter),
    enabled: Boolean(workspaceId),
    staleTime: STATUS_COUNTS_STALE_MS,
  });
}

export function useOrderDetail(
  workspaceId: string | undefined,
  orderId: string | null | undefined,
  enabled = true
) {
  return useQuery({
    queryKey: queryKeys.orderDetail(workspaceId || "__none__", orderId || ""),
    queryFn: () => Api.getOrderDetail(orderId!),
    enabled: Boolean(workspaceId) && Boolean(orderId) && enabled,
    staleTime: 15_000,
  });
}

export function useSyncOrders(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body?: { store_ids?: string[]; days?: number }) => Api.syncOrders(body),
    onSuccess: async () => {
      if (!workspaceId) return;
      await Promise.all([
        qc.invalidateQueries({
          queryKey: ["workspace", workspaceId, "unified-orders"],
        }),
        qc.invalidateQueries({
          queryKey: ["workspace", workspaceId, "order-status-counts"],
        }),
        qc.invalidateQueries({ queryKey: queryKeys.printJobs(workspaceId) }),
      ]);
    },
  });
}

export function useValidatePrint(workspaceId: string | undefined) {
  return useMutation({
    mutationFn: (orderIds: string[]) => Api.validatePrintLabels(orderIds),
  });
}

export function usePrintOrdersByIds(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      orderIds,
      allowReprint = false,
    }: {
      orderIds: string[];
      allowReprint?: boolean;
    }) => Api.printOrdersByIds(orderIds, allowReprint),
    onSuccess: async () => {
      if (!workspaceId) return;
      await Promise.all([
        qc.invalidateQueries({
          queryKey: ["workspace", workspaceId, "unified-orders"],
        }),
        qc.invalidateQueries({
          queryKey: ["workspace", workspaceId, "order-status-counts"],
        }),
        qc.invalidateQueries({
          queryKey: ["workspace", workspaceId, "order-detail"],
        }),
        qc.invalidateQueries({ queryKey: queryKeys.printJobs(workspaceId) }),
      ]);
    },
  });
}

/** Poll a print job until done/error or timeout. */
export async function pollPrintJob(
  jobId: string,
  onProgress?: (message: string) => void,
  maxWaitMs = 25 * 60 * 1000
) {
  const started = Date.now();
  while (Date.now() - started < maxWaitMs) {
    const status = await Api.printStatus(jobId);
    if (status.message) onProgress?.(status.message);
    if (status.status === "done" || status.status === "error") {
      return { ...status, id: jobId };
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error("Print timed out");
}
