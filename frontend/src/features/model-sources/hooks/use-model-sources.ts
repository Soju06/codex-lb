import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  createModelSource,
  deleteModelSource,
  listModelSources,
  updateModelSource,
} from "@/features/model-sources/api";
import type {
  ModelSourceCreateRequest,
  ModelSourceUpdateRequest,
} from "@/features/model-sources/schemas";

export function useModelSources() {
  const queryClient = useQueryClient();

  const { data, error, isFetching, isLoading, isPending, isSuccess, refetch } = useQuery({
    queryKey: ["model-sources", "list"],
    queryFn: listModelSources,
  });
  const modelSourcesQuery = { data, error, isFetching, isLoading, isPending, isSuccess, refetch };

  const createMutation = useMutation({
    mutationFn: (payload: ModelSourceCreateRequest) => createModelSource(payload),
    onSuccess: () => {
      toast.success("Model source created");
      void queryClient.invalidateQueries({ queryKey: ["model-sources", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to create model source");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ sourceId, payload }: { sourceId: string; payload: ModelSourceUpdateRequest }) =>
      updateModelSource(sourceId, payload),
    onSuccess: () => {
      toast.success("Model source updated");
      void queryClient.invalidateQueries({ queryKey: ["model-sources", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to update model source");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (sourceId: string) => deleteModelSource(sourceId),
    onSuccess: () => {
      toast.success("Model source deleted");
      void queryClient.invalidateQueries({ queryKey: ["model-sources", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to delete model source");
    },
  });

  return {
    modelSourcesQuery,
    createMutation,
    updateMutation,
    deleteMutation,
  };
}
