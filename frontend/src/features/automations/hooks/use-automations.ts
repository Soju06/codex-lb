import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
      toast.success("Automation created");
      await invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to create automation");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ automationId, payload }: { automationId: string; payload: AutomationUpdateRequest }) =>
      updateAutomation(automationId, payload),
    onSuccess: async () => {
      toast.success("Automation updated");
      await invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to update automation");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (automationId: string) => deleteAutomation(automationId),
    onSuccess: async () => {
      toast.success("Automation deleted");
      await invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to delete automation");
    },
  });

  const runNowMutation = useMutation({
    mutationFn: (automationId: string) => runAutomationNow(automationId),
    onSuccess: async () => {
      toast.success("Automation run queued");
      await invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to run automation");
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
