import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";

const PRINT_JOBS_STALE_MS = 15_000;

export function usePrintJobs(workspaceId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.printJobs(workspaceId || "__none__"),
    queryFn: async () => {
      const data = await Api.listPrintJobs();
      return data.jobs || [];
    },
    enabled: Boolean(workspaceId) && enabled,
    staleTime: PRINT_JOBS_STALE_MS,
  });
}

/**
 * RTS orders — disabled by default. Enable only after the user clicks Load Orders.
 * Never enable when storeIds is empty (empty selection ≠ all stores).
 */
export function useRtsOrders(
  workspaceId: string | undefined,
  storeIds: string[],
  limit: number,
  enabled = false
) {
  const canFetch = Boolean(workspaceId) && storeIds.length > 0 && enabled;
  return useQuery({
    queryKey: queryKeys.orders(workspaceId || "__none__", storeIds, limit),
    queryFn: async () => {
      const data = await Api.listOrders(storeIds, limit);
      return data;
    },
    enabled: canFetch,
    staleTime: 0,
    gcTime: 60_000,
    refetchOnMount: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
  });
}

export function useInvalidatePrintJobs(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return async () => {
    if (!workspaceId) return;
    await qc.invalidateQueries({ queryKey: queryKeys.printJobs(workspaceId) });
  };
}
