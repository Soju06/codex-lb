import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createModelOverride,
  deleteModelOverride,
  listModelOverrides,
  updateModelOverride,
} from "@/features/model-overrides/api";
import type {
  ModelOverrideCreateRequest,
  ModelOverrideUpdateRequest,
} from "@/features/model-overrides/schemas";

export function useModelOverrides() {
  const queryClient = useQueryClient();

  const overridesQuery = useQuery({
    queryKey: ["model-overrides", "list"],
    queryFn: listModelOverrides,
  });

  const createMutation = useMutation({
    mutationFn: (payload: ModelOverrideCreateRequest) => createModelOverride(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["model-overrides", "list"] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ overrideId, payload }: { overrideId: number; payload: ModelOverrideUpdateRequest }) =>
      updateModelOverride(overrideId, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["model-overrides", "list"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (overrideId: number) => deleteModelOverride(overrideId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["model-overrides", "list"] });
    },
  });

  return {
    overridesQuery,
    createMutation,
    updateMutation,
    deleteMutation,
  };
}

