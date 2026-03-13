import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  deleteAccount,
  downloadAccountsAuthArchive,
  getAccountTrends,
  importAccounts,
  listAccounts,
  pauseAccount,
  reactivateAccount,
} from "@/features/accounts/api";
import { downloadBlob } from "@/lib/download";

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
    mutationFn: importAccounts,
    onSuccess: (result) => {
      const importedCount = result.imported.length;
      const failedCount = result.failed.length;

      if (importedCount > 0) {
        invalidateAccountRelatedQueries(queryClient);
      }

      if (failedCount === 0) {
        toast.success(importedCount === 1 ? "Imported 1 account" : `Imported ${importedCount} accounts`);
        return;
      }

      if (importedCount === 0) {
        toast.error(failedCount === 1 ? "Import failed for 1 file" : `Import failed for ${failedCount} files`);
        return;
      }

      toast.success(`Imported ${importedCount} account${importedCount === 1 ? "" : "s"}, ${failedCount} failed`);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Import failed");
    },
  });

  const exportAuthArchiveMutation = useMutation({
    mutationFn: downloadAccountsAuthArchive,
    onSuccess: ({ blob, filename }) => {
      downloadBlob(blob, filename ?? "auth-export.zip");
      toast.success("Downloaded auth archive");
    },
    onError: (error: Error) => {
      toast.error(error.message || "Download failed");
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

  return { importMutation, exportAuthArchiveMutation, pauseMutation, resumeMutation, deleteMutation };
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
