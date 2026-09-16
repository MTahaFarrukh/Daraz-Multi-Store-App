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

export function useInvalidatePrintJobs(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return async () => {
    if (!workspaceId) return;
    await qc.invalidateQueries({ queryKey: queryKeys.printJobs(workspaceId) });
  };
}
