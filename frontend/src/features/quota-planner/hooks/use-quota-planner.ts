import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  cancelQuotaPlannerDecision,
  getQuotaPlannerForecast,
  getQuotaPlannerSettings,
  listQuotaPlannerDecisions,
  updateQuotaPlannerSettings,
  warmQuotaPlannerAccount,
} from "@/features/quota-planner/api";
import type { QuotaPlannerSettingsUpdateRequest, QuotaPlannerWarmNowRequest } from "@/features/quota-planner/schemas";

export function useQuotaPlanner() {
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["quota-planner", "settings"],
    queryFn: getQuotaPlannerSettings,
    refetchOnWindowFocus: true,
  });

  const decisionsQuery = useQuery({
    queryKey: ["quota-planner", "decisions"],
    queryFn: () => listQuotaPlannerDecisions(20),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });

  const forecastQuery = useQuery({
    queryKey: ["quota-planner", "forecast"],
    queryFn: () => getQuotaPlannerForecast(36),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });

  const updateSettingsMutation = useMutation({
    mutationFn: (payload: QuotaPlannerSettingsUpdateRequest) => updateQuotaPlannerSettings(payload),
    onSuccess: async () => {
      toast.success("Quota planner settings saved");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["quota-planner", "settings"] }),
        queryClient.invalidateQueries({ queryKey: ["quota-planner", "forecast"] }),
        queryClient.invalidateQueries({ queryKey: ["quota-planner", "decisions"] }),
      ]);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to save quota planner settings");
    },
  });

  const warmNowMutation = useMutation({
    mutationFn: (payload: QuotaPlannerWarmNowRequest) => warmQuotaPlannerAccount(payload),
    onSuccess: async (response) => {
      toast.success(`Warmup ${response.status}: ${response.reason}`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["quota-planner", "decisions"] }),
        queryClient.invalidateQueries({ queryKey: ["quota-planner", "forecast"] }),
      ]);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to request quota warmup");
    },
  });

  const cancelDecisionMutation = useMutation({
    mutationFn: (decisionId: string) => cancelQuotaPlannerDecision(decisionId),
    onSuccess: async () => {
      toast.success("Quota planner decision canceled");
      await queryClient.invalidateQueries({ queryKey: ["quota-planner", "decisions"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to cancel quota planner decision");
    },
  });

  return {
    settingsQuery,
    decisionsQuery,
    forecastQuery,
    updateSettingsMutation,
    warmNowMutation,
    cancelDecisionMutation,
  };
}
