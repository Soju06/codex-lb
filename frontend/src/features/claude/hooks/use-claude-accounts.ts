import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  addClaudeAccount,
  disableClaudeAccount,
  enableClaudeAccount,
  listClaudeAccounts,
} from "@/features/claude/api";
import type { AddClaudeAccountRequest } from "@/features/claude/schemas";

export function useClaudeAccounts() {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  const accountsQuery = useQuery({
    queryKey: ["claude-accounts", "list"],
    queryFn: listClaudeAccounts,
    select: (data) => data.accounts,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });

  const addMutation = useMutation({
    mutationFn: (payload: AddClaudeAccountRequest) => addClaudeAccount(payload),
    onSuccess: () => {
      toast.success(t("claude.toasts.added"));
      void queryClient.invalidateQueries({ queryKey: ["claude-accounts", "list"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("claude.errors.add"));
    },
  });

  const disableMutation = useMutation({
    mutationFn: ({ accountId, reason }: { accountId: string; reason?: string }) =>
      disableClaudeAccount(accountId, reason),
    onSuccess: () => {
      toast.success(t("claude.toasts.disabled"));
      void queryClient.invalidateQueries({ queryKey: ["claude-accounts", "list"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("claude.errors.disable"));
    },
  });

  const enableMutation = useMutation({
    mutationFn: (accountId: string) => enableClaudeAccount(accountId),
    onSuccess: () => {
      toast.success(t("claude.toasts.enabled"));
      void queryClient.invalidateQueries({ queryKey: ["claude-accounts", "list"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("claude.errors.enable"));
    },
  });

  return { accountsQuery, addMutation, disableMutation, enableMutation };
}