import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { queryKeys, type PerformanceMetric } from "@/lib/queryKeys";

const PERF_STALE_MS = 30_000;
const MONTHS_STALE_MS = 60_000;

export function usePerformanceMonths(workspaceId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.performanceMonths(workspaceId || "__none__"),
    queryFn: () => Api.performanceMonths(),
    enabled: Boolean(workspaceId),
    staleTime: MONTHS_STALE_MS,
  });
}

export function useStorePerformance(
  workspaceId: string | undefined,
  year: number | null,
  month: number | null,
  metric: PerformanceMetric
) {
  const ready = Boolean(workspaceId) && year != null && month != null;
  return useQuery({
    queryKey: queryKeys.performance(
      workspaceId || "__none__",
      year ?? 0,
      month ?? 0,
      metric
    ),
    queryFn: () => Api.storePerformance(year!, month!, metric),
    enabled: ready,
    staleTime: PERF_STALE_MS,
    // Do NOT keepPreviousData across month/metric keys — that would mislabel periods.
  });
}

export function useSyncStorePerformance(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      year,
      month,
      includeGrossSales = true,
    }: {
      year: number;
      month: number;
      includeGrossSales?: boolean;
    }) => Api.syncStorePerformance(year, month, includeGrossSales),
    onSuccess: async (_data, vars) => {
      if (!workspaceId) return;
      await Promise.all([
        qc.invalidateQueries({
          queryKey: queryKeys.performancePeriod(workspaceId, vars.year, vars.month),
        }),
        qc.invalidateQueries({ queryKey: queryKeys.performanceMonths(workspaceId) }),
      ]);
    },
  });
}
