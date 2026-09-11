import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const { data, error, isFetching, isLoading, isPending, isSuccess, refetch } = useQuery({
    queryKey: ["model-sources", "list"],
    queryFn: listModelSources,
  });
  const modelSourcesQuery = { data, error, isFetching, isLoading, isPending, isSuccess, refetch };

  const createMutation = useMutation({
    mutationFn: (payload: ModelSourceCreateRequest) => createModelSource(payload),
    onSuccess: () => {
      toast.success(t("modelSources.toasts.created"));
      void queryClient.invalidateQueries({ queryKey: ["model-sources", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("modelSources.toasts.createFailed"));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ sourceId, payload }: { sourceId: string; payload: ModelSourceUpdateRequest }) =>
      updateModelSource(sourceId, payload),
    onSuccess: () => {
      toast.success(t("modelSources.toasts.updated"));
      void queryClient.invalidateQueries({ queryKey: ["model-sources", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["models"] });
      // The subscription-overflow preflight reports this source's model
      // entries and Responses support; the Routing card keeps it mounted while
      // the operator edits the source on the same page, so refetch it here
      // rather than only on a settings save.
      void queryClient.invalidateQueries({ queryKey: ["settings", "subscription-overflow-preflight"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("modelSources.toasts.updateFailed"));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (sourceId: string) => deleteModelSource(sourceId),
    onSuccess: () => {
      toast.success(t("modelSources.toasts.deleted"));
      void queryClient.invalidateQueries({ queryKey: ["model-sources", "list"] });
      // Deleting the designated subscription-overflow source clears that
      // setting server-side; refetch so the routing card reflects it, and drop
      // any preflight report that still names the deleted source.
      void queryClient.invalidateQueries({ queryKey: ["settings", "detail"] });
      void queryClient.invalidateQueries({ queryKey: ["settings", "subscription-overflow-preflight"] });
      void queryClient.invalidateQueries({ queryKey: ["api-keys", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("modelSources.toasts.deleteFailed"));
    },
  });

  return {
    modelSourcesQuery,
    createMutation,
    updateMutation,
    deleteMutation,
  };
}
