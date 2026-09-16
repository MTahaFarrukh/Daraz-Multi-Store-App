import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { normalizeOrdersFilterKey, queryKeys } from "@/lib/queryKeys";
import type { ProductListParams } from "@/types/api";

const STALE_MS = 30_000;

export function useProducts(
  workspaceId: string | undefined,
  filters: ProductListParams,
  options?: { enabled?: boolean }
) {
  const filterKey = normalizeOrdersFilterKey(
    filters as Record<string, string | number | null | undefined>
  );
  return useQuery({
    queryKey: queryKeys.products(workspaceId || "__none__", filterKey),
    queryFn: () => Api.listProducts(filters),
    enabled: Boolean(workspaceId) && options?.enabled !== false,
    staleTime: STALE_MS,
    placeholderData: (prev) => prev,
  });
}

export function useProduct(
  workspaceId: string | undefined,
  productId: string | null | undefined,
  enabled = true
) {
  return useQuery({
    queryKey: queryKeys.product(workspaceId || "__none__", productId || ""),
    queryFn: () => Api.getProduct(productId!),
    enabled: Boolean(workspaceId) && Boolean(productId) && enabled,
    staleTime: 15_000,
  });
}

export function useProductDefaults(workspaceId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.productDefaults(workspaceId || "__none__"),
    queryFn: () => Api.getProductDefaults(),
    enabled: Boolean(workspaceId),
    staleTime: STALE_MS,
  });
}

export function useSyncProducts(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body?: { store_ids?: string[]; fetch_details?: boolean }) =>
      Api.syncProducts(body),
    onSuccess: async () => {
      if (!workspaceId) return;
      await qc.invalidateQueries({
        queryKey: ["workspace", workspaceId, "products"],
      });
      await qc.invalidateQueries({
        queryKey: ["workspace", workspaceId, "product"],
      });
    },
  });
}

export function useSaveProductDefaults(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: Api.saveProductDefaults,
    onSuccess: async () => {
      if (!workspaceId) return;
      await qc.invalidateQueries({
        queryKey: queryKeys.productDefaults(workspaceId),
      });
      await qc.invalidateQueries({
        queryKey: ["workspace", workspaceId, "clone-draft"],
      });
    },
  });
}

export function usePrepareCloneDraft(_workspaceId: string | undefined) {
  return useMutation({
    mutationFn: ({
      productId,
      destinationStoreId,
    }: {
      productId: string;
      destinationStoreId: string;
    }) => Api.prepareCloneDraft(productId, destinationStoreId),
  });
}

export function useFetchConnectedProduct(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { source_store_id: string; daraz_item_id: string }) =>
      Api.fetchConnectedProduct(body),
    onSuccess: async () => {
      if (!workspaceId) return;
      await qc.invalidateQueries({
        queryKey: ["workspace", workspaceId, "products"],
      });
      await qc.invalidateQueries({
        queryKey: ["workspace", workspaceId, "product"],
      });
    },
  });
}

export function useCloneDraftFromConnected(_workspaceId: string | undefined) {
  return useMutation({
    mutationFn: (body: {
      source_store_id: string;
      daraz_item_id: string;
      destination_store_id: string;
    }) => Api.cloneDraftFromConnected(body),
  });
}

export function useImportUrlDraft(_workspaceId: string | undefined) {
  return useMutation({
    mutationFn: (body: { url: string; destination_store_id: string }) =>
      Api.importUrlDraft(body),
  });
}

export function useEnsureProductDetail(workspaceId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (productId: string) => Api.ensureProductDetail(productId),
    onSuccess: async (_data, productId) => {
      if (!workspaceId) return;
      await qc.invalidateQueries({
        queryKey: queryKeys.product(workspaceId, productId),
      });
      await qc.invalidateQueries({
        queryKey: ["workspace", workspaceId, "products"],
      });
    },
  });
}
