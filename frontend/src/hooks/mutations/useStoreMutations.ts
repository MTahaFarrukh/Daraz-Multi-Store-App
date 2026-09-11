import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";

export function useRenameStore(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ storeId, displayName }: { storeId: string; displayName: string }) =>
      Api.renameStore(storeId, displayName),
    onSuccess: async () => {
      if (!workspaceId) return;
      await Promise.all([
        qc.invalidateQueries({ queryKey: queryKeys.stores(workspaceId) }),
        // Labels on leaderboard come from stores join — refresh performance boards.
        qc.invalidateQueries({
          queryKey: ["workspace", workspaceId, "store-performance"],
        }),
      ]);
    },
  });
}

export function useRefreshStoreConnection(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (storeIds: string[]) => Api.refreshTokens(storeIds, true),
    onSuccess: async () => {
      if (!workspaceId) return;
      await qc.invalidateQueries({ queryKey: queryKeys.stores(workspaceId) });
    },
  });
}

export function useImportBrowserProfiles(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (profiles: Record<string, string[]>) => Api.importBrowserProfiles(profiles),
    onSuccess: async () => {
      if (!workspaceId) return;
      await qc.invalidateQueries({ queryKey: queryKeys.storeGroups(workspaceId) });
    },
  });
}
