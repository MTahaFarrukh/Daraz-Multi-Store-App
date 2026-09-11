import { useQuery } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";

const STORES_STALE_MS = 60_000;

export function useStores(workspaceId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.stores(workspaceId || "__none__"),
    queryFn: async () => {
      const data = await Api.listStores();
      return data.stores || [];
    },
    enabled: Boolean(workspaceId),
    staleTime: STORES_STALE_MS,
  });
}
