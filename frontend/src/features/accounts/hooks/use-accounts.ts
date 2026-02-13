import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteAccount,
  importAccount,
  listAccounts,
  pauseAccount,
  reactivateAccount,
} from "@/features/accounts/api";

function invalidateAccountRelatedQueries(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["accounts", "list"] });
  void queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] });
}

export function useAccounts() {
  const queryClient = useQueryClient();

  const accountsQuery = useQuery({
    queryKey: ["accounts", "list"],
    queryFn: listAccounts,
    select: (data) => data.accounts,
  });

  const importMutation = useMutation({
    mutationFn: importAccount,
    onSuccess: () => {
      invalidateAccountRelatedQueries(queryClient);
    },
  });

  const pauseMutation = useMutation({
    mutationFn: pauseAccount,
    onSuccess: () => {
      invalidateAccountRelatedQueries(queryClient);
    },
  });

  const resumeMutation = useMutation({
    mutationFn: reactivateAccount,
    onSuccess: () => {
      invalidateAccountRelatedQueries(queryClient);
    },
  });

  const reactivateMutation = useMutation({
    mutationFn: reactivateAccount,
    onSuccess: () => {
      invalidateAccountRelatedQueries(queryClient);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteAccount,
    onSuccess: () => {
      invalidateAccountRelatedQueries(queryClient);
    },
  });

  return {
    accountsQuery,
    importMutation,
    pauseMutation,
    resumeMutation,
    reactivateMutation,
    deleteMutation,
  };
}
