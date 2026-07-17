import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  createAutomation,
  deleteAutomation,
  listAutomationRuns,
  listAutomations,
  runAutomationNow,
  updateAutomation,
} from "@/features/automations/api";
import type {
  AutomationCreateRequest,
  AutomationUpdateRequest,
} from "@/features/automations/schemas";

type UseAutomationsOptions = {
  enableQueries?: boolean;
};

export function useAutomations(
  selectedAutomationId: string | null,
  options: UseAutomationsOptions = {},
) {
  const { t } = useTranslation();
  const enableQueries = options.enableQueries ?? true;
  const queryClient = useQueryClient();

  const automationsQuery = useQuery({
    queryKey: ["automations", "list"],
    queryFn: () => listAutomations(),
    enabled: enableQueries,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });

  const effectiveSelectedAutomationId =
    selectedAutomationId ?? automationsQuery.data?.items[0]?.id ?? null;

  const runsQuery = useQuery({
    queryKey: ["automations", "runs", effectiveSelectedAutomationId],
    queryFn: () => listAutomationRuns(effectiveSelectedAutomationId ?? "", 20),
    enabled: enableQueries && effectiveSelectedAutomationId != null,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["automations"] });
  };

  const createMutation = useMutation({
    mutationFn: (payload: AutomationCreateRequest) => createAutomation(payload),
    onSuccess: async () => {
      toast.success(t("automations.toasts.created"));
      await invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || t("automations.toasts.createFailed"));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ automationId, payload }: { automationId: string; payload: AutomationUpdateRequest }) =>
      updateAutomation(automationId, payload),
    onSuccess: async () => {
      toast.success(t("automations.toasts.updated"));
      await invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || t("automations.toasts.updateFailed"));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (automationId: string) => deleteAutomation(automationId),
    onSuccess: async () => {
      toast.success(t("automations.toasts.deleted"));
      await invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || t("automations.toasts.deleteFailed"));
    },
  });

  const runNowMutation = useMutation({
    mutationFn: (automationId: string) => runAutomationNow(automationId),
    onSuccess: async () => {
      toast.success(t("automations.toasts.runQueued"));
      await invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || t("automations.toasts.runFailed"));
    },
  });

  return {
    automationsQuery,
    runsQuery,
    effectiveSelectedAutomationId,
    createMutation,
    updateMutation,
    deleteMutation,
    runNowMutation,
  };
}
