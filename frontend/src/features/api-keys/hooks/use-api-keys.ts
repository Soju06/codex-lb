import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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

export function useApiKeys() {
  const queryClient = useQueryClient();

  const apiKeysQuery = useQuery({
    queryKey: ["api-keys", "list"],
    queryFn: listApiKeys,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["api-keys", "list"] });
  };

  const createMutation = useMutation({
    mutationFn: (payload: ApiKeyCreateRequest) => createApiKey(payload),
    onSuccess: invalidate,
  });

  const updateMutation = useMutation({
    mutationFn: ({ keyId, payload }: { keyId: string; payload: ApiKeyUpdateRequest }) =>
      updateApiKey(keyId, payload),
    onSuccess: invalidate,
  });

  const deleteMutation = useMutation({
    mutationFn: (keyId: string) => deleteApiKey(keyId),
    onSuccess: invalidate,
  });

  const regenerateMutation = useMutation({
    mutationFn: (keyId: string) => regenerateApiKey(keyId),
    onSuccess: invalidate,
  });

  return {
    apiKeysQuery,
    createMutation,
    updateMutation,
    deleteMutation,
    regenerateMutation,
  };
}
