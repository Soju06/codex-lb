import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  createApiKey,
  deleteApiKey,
  listApiKeys,
  regenerateApiKey,
  updateApiKey,
} from "@/features/api-keys/api";
import type {
  ApiKeyCreateRequest,
  ApiKeyUpdateRequest,
} from "@/features/api-keys/schemas";
import { getApiKeyTrends, getApiKeyUsage7Day } from "@/features/apis/api";

export function useApiKeys() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const { data, error, isFetching, isLoading, isPending, isSuccess, refetch } = useQuery({
    queryKey: ["api-keys", "list"],
    queryFn: listApiKeys,
    select: (data) => data,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
  const apiKeysQuery = { data, error, isFetching, isLoading, isPending, isSuccess, refetch };

  const createMutation = useMutation({
    mutationFn: (payload: ApiKeyCreateRequest) => createApiKey(payload),
    onSuccess: () => {
      toast.success(t("apiKeys.toasts.created"));
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "trends"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("apiKeys.toasts.createFailed"));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ keyId, payload }: { keyId: string; payload: ApiKeyUpdateRequest }) =>
      updateApiKey(keyId, payload),
    onSuccess: () => {
      toast.success(t("apiKeys.toasts.updated"));
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "trends"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("apiKeys.toasts.updateFailed"));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (keyId: string) => deleteApiKey(keyId),
    onSuccess: () => {
      toast.success(t("apiKeys.toasts.deleted"));
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "trends"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("apiKeys.toasts.deleteFailed"));
    },
  });

  const regenerateMutation = useMutation({
    mutationFn: (keyId: string) => regenerateApiKey(keyId),
    onSuccess: () => {
      toast.success(t("apiKeys.toasts.regenerated"));
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "trends"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("apiKeys.toasts.regenerateFailed"));
    },
  });

  return {
    apiKeysQuery,
    createMutation,
    updateMutation,
    deleteMutation,
    regenerateMutation,
  };
}

export function useApiKeyTrends(keyId: string | null) {
  return useQuery({
    queryKey: ["api-keys", "trends", keyId],
    queryFn: () => getApiKeyTrends(keyId!),
    enabled: !!keyId,
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
    refetchIntervalInBackground: false,
  });
}

export function useApiKeyUsage7Day(keyId: string | null) {
  return useQuery({
    queryKey: ["api-keys", "usage-7d", keyId],
    queryFn: () => getApiKeyUsage7Day(keyId!),
    enabled: !!keyId,
    staleTime: 2 * 60_000,
    refetchInterval: 2 * 60_000,
    refetchIntervalInBackground: false,
  });
}
