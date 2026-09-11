import { useQuery } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";

const GROUPS_STALE_MS = 60_000;

export function useStoreGroups(workspaceId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.storeGroups(workspaceId || "__none__"),
    queryFn: async () => {
      const data = await Api.listGroups();
      return data.groups || [];
    },
    enabled: Boolean(workspaceId),
    staleTime: GROUPS_STALE_MS,
  });
}
