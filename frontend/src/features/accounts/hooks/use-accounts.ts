import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  deleteAccount,
  exportAccountAuth,
  getAccountTrends,
  importAccount,
  listAccounts,
  pauseAccount,
  reactivateAccount,
  probeAccount,
  setAccountAlias,
  updateAccount,
  updateAccountLimitWarmup,
  updateAccountRoutingPolicy,
} from "@/features/accounts/api";
import type { AccountRoutingPolicy } from "@/features/accounts/schemas";

function invalidateAccountRelatedQueries(queryClient: ReturnType<typeof useQueryClient>, accountId?: string) {
  void queryClient.invalidateQueries({ queryKey: ["accounts", "list"] });
  void queryClient.invalidateQueries({ queryKey: ["accounts", "trends"] });
  void queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] });
  void queryClient.invalidateQueries({ queryKey: ["dashboard", "projections"] });
  if (accountId) {
    void queryClient.invalidateQueries({ queryKey: ["accounts", "trends", accountId] });
  }
}

/**
 * Account mutation actions without the polling query.
 * Use this when you need account actions but already have account data
 * from another source (e.g. the dashboard overview query).
 */
export function useAccountMutations() {
  const queryClient = useQueryClient();

  const importMutation = useMutation({
    mutationFn: importAccount,
    onSuccess: () => {
      toast.success("Account imported");
      invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Import failed");
    },
  });

  const pauseMutation = useMutation({
    mutationFn: pauseAccount,
    onSuccess: () => {
      toast.success("Account paused");
      invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Pause failed");
    },
  });

  const resumeMutation = useMutation({
    mutationFn: reactivateAccount,
    onSuccess: () => {
      toast.success("Account resumed");
      invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Resume failed");
    },
  });

  const setAliasMutation = useMutation({
    mutationFn: ({ accountId, alias }: { accountId: string; alias: string | null }) =>
      setAccountAlias(accountId, alias),
    onSuccess: () => {
      toast.success("Account alias updated");
      invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Alias update failed");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: ({ accountId, deleteHistory }: { accountId: string; deleteHistory: boolean }) =>
      deleteAccount(accountId, deleteHistory),
    onSuccess: () => {
      toast.success("Account deleted");
      invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Delete failed");
    },
  });

  const probeMutation = useMutation({
    mutationFn: ({ accountId, model }: { accountId: string; model?: string }) =>
      probeAccount(accountId, model ? { model } : undefined),
    onSuccess: (_data, variables) => {
      toast.success("Account probed");
      invalidateAccountRelatedQueries(queryClient, variables.accountId);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Probe failed");
    },
  });

  const limitWarmupMutation = useMutation({
    mutationFn: ({ accountId, enabled }: { accountId: string; enabled: boolean }) =>
      updateAccountLimitWarmup(accountId, enabled),
    onSuccess: (data) => {
      toast.success(data.enabled ? "Limit warm-up enabled" : "Limit warm-up disabled");
      invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Limit warm-up update failed");
    },
  });

  const routingPolicyMutation = useMutation({
    mutationFn: ({
      accountId,
      routingPolicy,
    }: {
      accountId: string;
      routingPolicy: AccountRoutingPolicy;
    }) => updateAccountRoutingPolicy(accountId, routingPolicy),
    onSuccess: (data) => {
      const label =
        data.routingPolicy === "normal" ? "normal" : data.routingPolicy.replace("_", "-");
      toast.success(`Account routing policy set to ${label}`);
      invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Routing policy update failed");
    },
  });

  const exportAuthMutation = useMutation({
    mutationFn: exportAccountAuth,
    onSuccess: () => {
      toast.success("Account exported");
    },
    onError: (error: Error) => {
      toast.error(error.message || "Export failed");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ accountId, securityWorkAuthorized }: { accountId: string; securityWorkAuthorized: boolean }) =>
      updateAccount(accountId, { securityWorkAuthorized }),
    onSuccess: () => {
      toast.success("Account updated");
      invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Update failed");
    },
  });

  return {
    importMutation,
    pauseMutation,
    resumeMutation,
    setAliasMutation,
    deleteMutation,
    probeMutation,
    exportAuthMutation,
    limitWarmupMutation,
    routingPolicyMutation,
    updateMutation,
  };
}

export function useAccountTrends(accountId: string | null) {
  return useQuery({
    queryKey: ["accounts", "trends", accountId],
    queryFn: () => getAccountTrends(accountId!),
    enabled: !!accountId,
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
    refetchIntervalInBackground: false,
  });
}

export function useAccounts() {
  const accountsQuery = useQuery({
    queryKey: ["accounts", "list"],
    queryFn: listAccounts,
    select: (data) => data.accounts,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });

  const mutations = useAccountMutations();

  return { accountsQuery, ...mutations };
}
