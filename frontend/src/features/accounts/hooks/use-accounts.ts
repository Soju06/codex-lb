import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { toast } from "sonner";

import {
  consumeRateLimitResetCredit,
  consumeAccountUsageResetCredit,
  deleteAccount,
  exportAccountAuth,
  getAccountTrends,
  getAccountUsageResetCredits,
  getRateLimitResetCredits,
  importAccount,
  listAccounts,
  pauseAccount,
  probeAccount,
  reactivateAccount,
  setAccountAlias,
  updateAccount,
  updateAccountLimitWarmup,
  updateAccountRoutingPolicy,
} from "@/features/accounts/api";
import type {
  AccountRoutingPolicy,
  AccountUsageResetConsumeResponse,
} from "@/features/accounts/schemas";

async function invalidateAccountRelatedQueries(queryClient: ReturnType<typeof useQueryClient>, accountId?: string) {
  const invalidations = [
    queryClient.invalidateQueries({ queryKey: ["accounts", "list"] }),
    queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] }),
    queryClient.invalidateQueries({ queryKey: ["dashboard", "projections"] }),
  ];
  if (accountId) {
    invalidations.push(queryClient.invalidateQueries({ queryKey: ["accounts", "trends", accountId] }));
    invalidations.push(queryClient.invalidateQueries({ queryKey: ["accounts", "usage-reset-credits", accountId] }));
  } else {
    invalidations.push(queryClient.invalidateQueries({ queryKey: ["accounts", "trends"] }));
    invalidations.push(queryClient.invalidateQueries({ queryKey: ["accounts", "usage-reset-credits"] }));
  }
  await Promise.all(invalidations);
}

function usageResetToastMessage(data: AccountUsageResetConsumeResponse): string {
  const changed =
    data.primaryUsedPercentBefore !== data.primaryUsedPercentAfter ||
    data.secondaryUsedPercentBefore !== data.secondaryUsedPercentAfter ||
    data.accountStatusBefore !== data.accountStatusAfter;
  if (data.code === "reset") {
    return changed ? "Usage reset applied" : "Usage reset applied; upstream values are unchanged";
  }
  if (data.code === "already_redeemed") {
    return "Usage reset was already applied";
  }
  if (data.code === "no_credit") {
    return "No usage reset credits available";
  }
  if (data.code === "nothing_to_reset") {
    return "Nothing to reset";
  }
  return "Usage reset request completed";
}

function createRedeemRequestId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `dashboard-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Account mutation actions without the polling query.
 * Use this when you need account actions but already have account data
 * from another source (e.g. the dashboard overview query).
 */
export function useAccountMutations() {
  const queryClient = useQueryClient();
  const usageResetRedeemRequestRef = useRef<{
    accountId: string;
    redeemRequestId: string;
  } | null>(null);

  const importMutation = useMutation({
    mutationFn: importAccount,
    onSuccess: () => {
      toast.success("Account imported");
      void invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Import failed");
    },
  });

  const pauseMutation = useMutation({
    mutationFn: pauseAccount,
    onSuccess: () => {
      toast.success("Account paused");
      void invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Pause failed");
    },
  });

  const resumeMutation = useMutation({
    mutationFn: reactivateAccount,
    onSuccess: () => {
      toast.success("Account resumed");
      void invalidateAccountRelatedQueries(queryClient);
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
      void invalidateAccountRelatedQueries(queryClient);
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
      void invalidateAccountRelatedQueries(queryClient);
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
      void invalidateAccountRelatedQueries(queryClient, variables.accountId);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Probe failed");
    },
  });

  const usageResetMutation = useMutation({
    mutationFn: ({ accountId }: { accountId: string }) => {
      if (usageResetRedeemRequestRef.current?.accountId !== accountId) {
        usageResetRedeemRequestRef.current = {
          accountId,
          redeemRequestId: createRedeemRequestId(),
        };
      }
      return consumeAccountUsageResetCredit(accountId, {
        redeemRequestId: usageResetRedeemRequestRef.current.redeemRequestId,
      });
    },
    onSuccess: async (data, variables) => {
      usageResetRedeemRequestRef.current = null;
      await invalidateAccountRelatedQueries(queryClient, variables.accountId);
      toast.success(usageResetToastMessage(data));
    },
    onError: (error: Error) => {
      toast.error(error.message || "Usage reset failed");
    },
  });

  const limitWarmupMutation = useMutation({
    mutationFn: ({ accountId, enabled }: { accountId: string; enabled: boolean }) =>
      updateAccountLimitWarmup(accountId, enabled),
    onSuccess: (data) => {
      toast.success(data.enabled ? "Limit warm-up enabled" : "Limit warm-up disabled");
      void invalidateAccountRelatedQueries(queryClient);
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
      void invalidateAccountRelatedQueries(queryClient);
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
      void invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Update failed");
    },
  });

  const resetCreditConsumeMutation = useMutation({
    mutationFn: ({ accountId, redeemRequestId }: { accountId: string; redeemRequestId?: string }) =>
      consumeRateLimitResetCredit(accountId, redeemRequestId ? { redeemRequestId } : undefined),
    onSuccess: (data) => {
      const resetCount = data.windowsReset ?? 0;
      toast.success(
        `Rate-limit window${resetCount === 1 ? "" : "s"} reset (${resetCount})`,
      );
      void queryClient.invalidateQueries({ queryKey: ["accounts", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["accounts", "trends"] });
      void queryClient.invalidateQueries({ queryKey: ["accounts", "reset-credits"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard", "projections"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Reset credit redeem failed");
    },
  });

  return {
    importMutation,
    pauseMutation,
    resumeMutation,
    setAliasMutation,
    deleteMutation,
    probeMutation,
    usageResetMutation,
    exportAuthMutation,
    limitWarmupMutation,
    routingPolicyMutation,
    updateMutation,
    resetCreditConsumeMutation,
  };
}

export function useRateLimitResetCredits(
  accountId: string | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["accounts", "reset-credits", accountId],
    queryFn: () => getRateLimitResetCredits(accountId as string),
    enabled: enabled && !!accountId,
    staleTime: 0,
  });
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

export function useAccountUsageResetCredits(accountId: string | null) {
  return useQuery({
    queryKey: ["accounts", "usage-reset-credits", accountId],
    queryFn: () => getAccountUsageResetCredits(accountId!),
    enabled: !!accountId,
    staleTime: 60_000,
  });
}

export function useAccounts() {
  const { data, error, isFetching, isLoading, isPending, isSuccess, refetch } = useQuery({
    queryKey: ["accounts", "list"],
    queryFn: listAccounts,
    select: (data) => data.accounts,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
  const accountsQuery = { data, error, isFetching, isLoading, isPending, isSuccess, refetch };

  const mutations = useAccountMutations();

  return { accountsQuery, ...mutations };
}
