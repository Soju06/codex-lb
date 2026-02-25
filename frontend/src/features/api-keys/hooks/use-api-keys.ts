import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

export function useApiKeys(ownerUserId?: string) {
  const queryClient = useQueryClient();

  const apiKeysQuery = useQuery({
    queryKey: ["api-keys", "list", ownerUserId ?? "all"],
    queryFn: () => listApiKeys(ownerUserId),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["api-keys", "list"] });
  };

  const createMutation = useMutation({
    mutationFn: (payload: ApiKeyCreateRequest) => createApiKey(payload),
    onSuccess: () => {
      toast.success("API key created");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to create API key");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ keyId, payload }: { keyId: string; payload: ApiKeyUpdateRequest }) =>
      updateApiKey(keyId, payload),
    onSuccess: () => {
      toast.success("API key updated");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to update API key");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (keyId: string) => deleteApiKey(keyId),
    onSuccess: () => {
      toast.success("API key deleted");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to delete API key");
    },
  });

  const regenerateMutation = useMutation({
    mutationFn: (keyId: string) => regenerateApiKey(keyId),
    onSuccess: () => {
      toast.success("API key regenerated");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to regenerate API key");
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
