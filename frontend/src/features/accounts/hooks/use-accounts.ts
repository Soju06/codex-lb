import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  createPlatformIdentity,
  deleteAccount,
  getAccountTrends,
  importAccount,
  listAccounts,
  pauseAccount,
  reactivateAccount,
  updatePlatformIdentity,
} from "@/features/accounts/api";
import type { PlatformIdentityUpdateRequest } from "@/features/accounts/schemas";

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

  const createPlatformMutation = useMutation({
    mutationFn: createPlatformIdentity,
    onSuccess: () => {
      toast.success("OpenAI Platform identity added");
      invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to add OpenAI Platform identity");
    },
  });

  const updatePlatformMutation = useMutation({
    mutationFn: ({
      accountId,
      payload,
    }: {
      accountId: string;
      payload: PlatformIdentityUpdateRequest;
    }) => updatePlatformIdentity(accountId, payload),
    onSuccess: () => {
      toast.success("OpenAI Platform identity updated");
      invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to update OpenAI Platform identity");
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

  const deleteMutation = useMutation({
    mutationFn: deleteAccount,
    onSuccess: () => {
      toast.success("Account deleted");
      invalidateAccountRelatedQueries(queryClient);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Delete failed");
    },
  });

  return {
    importMutation,
    createPlatformMutation,
    updatePlatformMutation,
    pauseMutation,
    resumeMutation,
    deleteMutation,
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
