import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";

export function useCreateStoreGroup(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, storeIds }: { name: string; storeIds: string[] }) =>
      Api.createGroup(name, storeIds),
    onSuccess: async () => {
      if (!workspaceId) return;
      await qc.invalidateQueries({ queryKey: queryKeys.storeGroups(workspaceId) });
    },
  });
}

export function useUpdateStoreGroup(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      groupId,
      name,
      storeIds,
    }: {
      groupId: string;
      name?: string;
      storeIds?: string[];
    }) => Api.updateGroup(groupId, { name, store_ids: storeIds }),
    onSuccess: async () => {
      if (!workspaceId) return;
      await qc.invalidateQueries({ queryKey: queryKeys.storeGroups(workspaceId) });
    },
  });
}

export function useDeleteStoreGroup(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (groupId: string) => Api.deleteGroup(groupId),
    onSuccess: async () => {
      if (!workspaceId) return;
      await qc.invalidateQueries({ queryKey: queryKeys.storeGroups(workspaceId) });
    },
  });
}
