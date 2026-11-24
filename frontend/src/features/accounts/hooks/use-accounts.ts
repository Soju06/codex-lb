import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { useTranslation } from "react-i18next";
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
  updateAccountUsageLimit,
} from "@/features/accounts/api";
import type {
  AccountRoutingPolicy,
  AccountSummary,
  AccountUsageLimitUpdateRequest,
  AccountUsageLimitUpdateResponse,
  AccountUsageResetConsumeResponse,
} from "@/features/accounts/schemas";
import type { DashboardOverview } from "@/features/dashboard/schemas";

// Patch the acknowledged policy into every cached account list so an older read cannot show the
// previous policy. Until a refetch returns the server's evaluation, an enabled policy reads as
// unavailable rather than keeping a stale "available" state.
function reconcileUsageLimitCaches(
  queryClient: ReturnType<typeof useQueryClient>,
  policy: AccountUsageLimitUpdateResponse,
) {
  const patch = <T extends { accounts: AccountSummary[] }>(current: T | undefined) =>
    current && {
      ...current,
      accounts: current.accounts.map((account) =>
        account.accountId === policy.accountId
          ? {
              ...account,
              usageLimitEnabled: policy.enabled,
              usageLimitPercent: policy.percent,
              usageLimit5HPercent: policy.percent5H,
              usageLimitWeeklyPercent: policy.percentWeekly,
              effectiveLimitPrimary: undefined,
              effectiveLimitSecondary: undefined,
              effectiveLimitMonthly: undefined,
              usageLimitState: policy.enabled ? "data_unavailable" : "disabled",
            }
          : account,
      ),
    };
  queryClient.setQueryData<{ accounts: AccountSummary[] }>(["accounts", "list"], patch);
  queryClient.setQueriesData<DashboardOverview>({ queryKey: ["dashboard", "overview"] }, patch);
}

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

function usageResetToastMessage(data: AccountUsageResetConsumeResponse, t: ReturnType<typeof useTranslation>["t"]): string {
  const changed =
    data.primaryUsedPercentBefore !== data.primaryUsedPercentAfter ||
    data.secondaryUsedPercentBefore !== data.secondaryUsedPercentAfter ||
    data.accountStatusBefore !== data.accountStatusAfter;
  if (data.code === "reset") {
    return changed ? t("accounts.toasts.usageResetApplied") : t("accounts.toasts.usageResetAppliedUnchanged");
  }
  if (data.code === "already_redeemed") {
    return t("accounts.toasts.usageResetAlreadyApplied");
  }
  if (data.code === "no_credit") {
    return t("accounts.toasts.noUsageResetCredits");
  }
  if (data.code === "nothing_to_reset") {
    return t("accounts.toasts.nothingToReset");
  }
  return t("accounts.toasts.usageResetCompleted");
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
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const usageResetRedeemRequestRef = useRef<{
    accountId: string;
    redeemRequestId: string;
  } | null>(null);

  const importMutation = useMutation({
    mutationFn: importAccount,
    onSuccess: () => {
      toast.success(t("accounts.toasts.imported"));
      void invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.importFailed"));
    },
  });

  const pauseMutation = useMutation({
    mutationFn: pauseAccount,
    onSuccess: () => {
      toast.success(t("accounts.toasts.paused"));
      void invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.pauseFailed"));
    },
  });

  const resumeMutation = useMutation({
    mutationFn: reactivateAccount,
    onSuccess: () => {
      toast.success(t("accounts.toasts.resumed"));
      void invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.resumeFailed"));
    },
  });

  const setAliasMutation = useMutation({
    mutationFn: ({ accountId, alias }: { accountId: string; alias: string | null }) =>
      setAccountAlias(accountId, alias),
    onSuccess: () => {
      toast.success(t("accounts.toasts.aliasUpdated"));
      void invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.aliasUpdateFailed"));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: ({ accountId, deleteHistory }: { accountId: string; deleteHistory: boolean }) =>
      deleteAccount(accountId, deleteHistory),
    onSuccess: () => {
      toast.success(t("accounts.toasts.deleted"));
      void invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.deleteFailed"));
    },
  });

  const probeMutation = useMutation({
    mutationFn: ({ accountId, model }: { accountId: string; model?: string }) =>
      probeAccount(accountId, model ? { model } : undefined),
    onSuccess: (_data, variables) => {
      toast.success(t("accounts.toasts.probed"));
      void invalidateAccountRelatedQueries(queryClient, variables.accountId);
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.probeFailed"));
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
      toast.success(usageResetToastMessage(data, t));
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.usageResetFailed"));
    },
  });

  const limitWarmupMutation = useMutation({
    mutationFn: ({ accountId, enabled }: { accountId: string; enabled: boolean }) =>
      updateAccountLimitWarmup(accountId, enabled),
    onSuccess: (data) => {
      toast.success(data.enabled ? t("accounts.toasts.limitWarmupEnabled") : t("accounts.toasts.limitWarmupDisabled"));
      void invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.limitWarmupUpdateFailed"));
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
      toast.success(t("accounts.toasts.routingPolicySet", { label }));
      void invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.routingPolicyUpdateFailed"));
    },
  });

  const usageLimitMutation = useMutation({
    // Every control shares these caches, so policy writes run one at a time.
    scope: { id: "account-usage-limit" },
    mutationFn: ({ accountId, update }: { accountId: string; update: AccountUsageLimitUpdateRequest }) =>
      updateAccountUsageLimit(accountId, update),
    onSuccess: async (data) => {
      // Cancel in-flight reads, including inactive ones that invalidation leaves running, so an
      // older response cannot overwrite the acknowledged policy.
      await Promise.all([
        queryClient.cancelQueries({ queryKey: ["accounts", "list"] }),
        queryClient.cancelQueries({ queryKey: ["dashboard", "overview"] }),
      ]);
      reconcileUsageLimitCaches(queryClient, data);
      const removed = data.percent === null && data.percent5H === null && data.percentWeekly === null;
      toast.success(t(
        removed
          ? "accounts.toasts.usageLimitRemoved"
          : data.enabled ? "accounts.toasts.usageLimitEnabled" : "accounts.toasts.usageLimitDisabled",
      ));
      void invalidateAccountRelatedQueries(queryClient, data.accountId);
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.usageLimitUpdateFailed"));
    },
  });

  const exportAuthMutation = useMutation({
    mutationFn: exportAccountAuth,
    onSuccess: () => {
      toast.success(t("accounts.toasts.exported"));
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.exportFailed"));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ accountId, securityWorkAuthorized }: { accountId: string; securityWorkAuthorized: boolean }) =>
      updateAccount(accountId, { securityWorkAuthorized }),
    onSuccess: () => {
      toast.success(t("accounts.toasts.updated"));
      void invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.updateFailed"));
    },
  });

  const resetCreditConsumeMutation = useMutation({
    mutationFn: ({ accountId, redeemRequestId }: { accountId: string; redeemRequestId?: string }) =>
      consumeRateLimitResetCredit(accountId, redeemRequestId ? { redeemRequestId } : undefined),
    onSuccess: (data) => {
      const resetCount = data.windowsReset ?? 0;
      toast.success(
        t("accounts.toasts.rateLimitWindowsReset", { count: resetCount }),
      );
      void queryClient.invalidateQueries({ queryKey: ["accounts", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["accounts", "trends"] });
      void queryClient.invalidateQueries({ queryKey: ["accounts", "reset-credits"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard", "projections"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.toasts.resetCreditRedeemFailed"));
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
    usageLimitMutation,
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
