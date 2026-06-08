import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  createAntigravityProviderAccount,
  createGeminiProviderAccount,
  getAgentProviderAccounts,
  getAgentProviderOverview,
  getAgentProviderRoutingSettings,
  getAgentProviders,
  preflightAgentProviderRouting,
  runAntigravityHarnessPrint,
  runAntigravityManagedInteraction,
  updateAgentProviderAccount,
  updateAgentProviderRoutingSettings,
  upsertAgentProviderQuotaWindow,
  type AgentProviderId,
} from "@/features/agent-providers/api";
import type {
  AgentProviderAccountUpdate,
  AntigravityProviderAccountCreate,
  GeminiProviderAccountCreate,
} from "@/features/agent-providers/accounts-schemas";
import type {
  AntigravityHarnessPrintRequest,
  AntigravityManagedInteractionRunRequest,
} from "@/features/agent-providers/harness-schemas";
import type {
  AgentProviderQuotaWindowUpsert,
  AgentProviderRoutingSettingsUpdate,
} from "@/features/agent-providers/routing-schemas";
import type { ProviderOverviewTimeframe } from "@/features/agent-providers/schemas";

export function useAgentProviderRegistry() {
  return useQuery({
    queryKey: ["agent-providers", "registry"],
    queryFn: getAgentProviders,
  });
}

export function useAgentProviderOverview(timeframe: ProviderOverviewTimeframe = "7d") {
  return useQuery({
    queryKey: ["agent-providers", "overview", timeframe],
    queryFn: () => getAgentProviderOverview(timeframe),
  });
}

export function useAgentProviderAccounts(providerId: AgentProviderId) {
  return useQuery({
    queryKey: ["agent-providers", providerId, "accounts"],
    queryFn: () => getAgentProviderAccounts(providerId),
  });
}

export function useAgentProviderRouting(providerId: AgentProviderId) {
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["agent-providers", providerId, "routing", "settings"],
    queryFn: () => getAgentProviderRoutingSettings(providerId),
  });

  const preflightQuery = useQuery({
    queryKey: ["agent-providers", providerId, "routing", "preflight"],
    queryFn: () => preflightAgentProviderRouting(providerId),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["agent-providers", providerId] });
    void queryClient.invalidateQueries({ queryKey: ["agent-providers", "overview"] });
  };

  const updateSettingsMutation = useMutation({
    mutationFn: (payload: AgentProviderRoutingSettingsUpdate) =>
      updateAgentProviderRoutingSettings(providerId, payload),
    onSuccess: () => {
      toast.success("Provider routing saved");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Provider routing update failed");
    },
  });

  const upsertQuotaWindowMutation = useMutation({
    mutationFn: ({
      accountId,
      dimension,
      payload,
    }: {
      accountId: string;
      dimension: string;
      payload: AgentProviderQuotaWindowUpsert;
    }) => upsertAgentProviderQuotaWindow(providerId, accountId, dimension, payload),
    onSuccess: () => {
      toast.success("Provider quota saved");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Provider quota update failed");
    },
  });

  return {
    settingsQuery,
    preflightQuery,
    updateSettingsMutation,
    upsertQuotaWindowMutation,
  };
}

export function useCreateGeminiProviderAccount() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: GeminiProviderAccountCreate) => createGeminiProviderAccount(payload),
    onSuccess: () => {
      toast.success("Gemini account added");
      void queryClient.invalidateQueries({ queryKey: ["agent-providers", "gemini"] });
      void queryClient.invalidateQueries({ queryKey: ["agent-providers", "overview"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Gemini account creation failed");
    },
  });
}

export function useCreateAntigravityProviderAccount() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: AntigravityProviderAccountCreate) => createAntigravityProviderAccount(payload),
    onSuccess: () => {
      toast.success("Antigravity profile added");
      void queryClient.invalidateQueries({ queryKey: ["agent-providers", "antigravity"] });
      void queryClient.invalidateQueries({ queryKey: ["agent-providers", "overview"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Antigravity profile creation failed");
    },
  });
}

export function useUpdateAgentProviderAccount(providerId: AgentProviderId) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ accountId, payload }: { accountId: string; payload: AgentProviderAccountUpdate }) =>
      updateAgentProviderAccount(providerId, accountId, payload),
    onSuccess: () => {
      toast.success("Provider account saved");
      void queryClient.invalidateQueries({ queryKey: ["agent-providers", providerId] });
      void queryClient.invalidateQueries({ queryKey: ["agent-providers", "overview"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Provider account update failed");
    },
  });
}

export function useAntigravityHarnessPrint() {
  return useMutation({
    mutationFn: (payload: AntigravityHarnessPrintRequest) => runAntigravityHarnessPrint(payload),
    onSuccess: () => {
      toast.success("Antigravity harness completed");
    },
    onError: (error: Error) => {
      toast.error(error.message || "Antigravity harness failed");
    },
  });
}

export function useAntigravityManagedInteraction() {
  return useMutation({
    mutationFn: (payload: AntigravityManagedInteractionRunRequest) => runAntigravityManagedInteraction(payload),
    onSuccess: () => {
      toast.success("Antigravity interaction completed");
    },
    onError: (error: Error) => {
      toast.error(error.message || "Antigravity interaction failed");
    },
  });
}
