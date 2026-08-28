import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const {
    data: settingsData,
    error: settingsError,
    isFetching: settingsIsFetching,
    isLoading: settingsIsLoading,
    isPending: settingsIsPending,
    isSuccess: settingsIsSuccess,
    refetch: refetchSettings,
  } = useQuery({
    queryKey: ["quota-planner", "settings"],
    queryFn: getQuotaPlannerSettings,
    refetchOnWindowFocus: true,
  });
  const settingsQuery = {
    data: settingsData,
    error: settingsError,
    isFetching: settingsIsFetching,
    isLoading: settingsIsLoading,
    isPending: settingsIsPending,
    isSuccess: settingsIsSuccess,
    refetch: refetchSettings,
  };

  const {
    data: decisionsData,
    error: decisionsError,
    isFetching: decisionsIsFetching,
    isLoading: decisionsIsLoading,
    isPending: decisionsIsPending,
    isSuccess: decisionsIsSuccess,
    refetch: refetchDecisions,
  } = useQuery({
    queryKey: ["quota-planner", "decisions"],
    queryFn: () => listQuotaPlannerDecisions(20),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
  const decisionsQuery = {
    data: decisionsData,
    error: decisionsError,
    isFetching: decisionsIsFetching,
    isLoading: decisionsIsLoading,
    isPending: decisionsIsPending,
    isSuccess: decisionsIsSuccess,
    refetch: refetchDecisions,
  };

  const {
    data: forecastData,
    error: forecastError,
    isFetching: forecastIsFetching,
    isLoading: forecastIsLoading,
    isPending: forecastIsPending,
    isSuccess: forecastIsSuccess,
    refetch: refetchForecast,
  } = useQuery({
    queryKey: ["quota-planner", "forecast"],
    queryFn: () => getQuotaPlannerForecast(36),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });
  const forecastQuery = {
    data: forecastData,
    error: forecastError,
    isFetching: forecastIsFetching,
    isLoading: forecastIsLoading,
    isPending: forecastIsPending,
    isSuccess: forecastIsSuccess,
    refetch: refetchForecast,
  };

  const updateSettingsMutation = useMutation({
    mutationFn: (payload: QuotaPlannerSettingsUpdateRequest) => updateQuotaPlannerSettings(payload),
    onSuccess: async () => {
      toast.success(t("quotaPlanner.toasts.saved"));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["quota-planner", "settings"] }),
        queryClient.invalidateQueries({ queryKey: ["quota-planner", "forecast"] }),
        queryClient.invalidateQueries({ queryKey: ["quota-planner", "decisions"] }),
      ]);
    },
    onError: (error: Error) => {
      toast.error(error.message || t("quotaPlanner.toasts.saveFailed"));
    },
  });

  const warmNowMutation = useMutation({
    mutationFn: (payload: QuotaPlannerWarmNowRequest) => warmQuotaPlannerAccount(payload),
    onSuccess: async (response) => {
      toast.success(t("quotaPlanner.toasts.warmupResult", { status: response.status, reason: response.reason }));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["quota-planner", "decisions"] }),
        queryClient.invalidateQueries({ queryKey: ["quota-planner", "forecast"] }),
      ]);
    },
    onError: (error: Error) => {
      toast.error(error.message || t("quotaPlanner.toasts.warmupFailed"));
    },
  });

  const cancelDecisionMutation = useMutation({
    mutationFn: (decisionId: string) => cancelQuotaPlannerDecision(decisionId),
    onSuccess: async () => {
      toast.success(t("quotaPlanner.toasts.canceled"));
      await queryClient.invalidateQueries({ queryKey: ["quota-planner", "decisions"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("quotaPlanner.toasts.cancelFailed"));
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
