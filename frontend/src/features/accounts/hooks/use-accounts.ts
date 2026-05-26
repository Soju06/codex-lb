import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  deleteAccount,
  exportAccount,
  getAccountTrends,
  importAccount,
  listAccounts,
  pauseAccount,
  reactivateAccount,
  setAccountAlias,
  updateAccountLimitWarmup,
} from "@/features/accounts/api";

function invalidateAccountRelatedQueries(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["accounts", "list"] });
  void queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] });
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

  const exportMutation = useMutation({
    mutationFn: exportAccount,
    onSuccess: (data) => {
      const blob = new Blob([data.authJson], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "auth.json";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      toast.success("Account exported");
    },
    onError: (error: Error) => {
      toast.error(error.message || "Export failed");
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

  return {
    importMutation,
    pauseMutation,
    resumeMutation,
    setAliasMutation,
    deleteMutation,
    exportMutation,
    limitWarmupMutation,
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
