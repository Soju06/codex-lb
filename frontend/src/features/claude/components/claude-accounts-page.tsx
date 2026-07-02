import { Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { useDialogState } from "@/hooks/use-dialog-state";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { AddClaudeAccountDialog } from "@/features/claude/components/add-claude-account-dialog";
import { ClaudeAccountList } from "@/features/claude/components/claude-account-list";
import { ClaudeAccountUsageCard } from "@/features/claude/components/claude-account-usage-card";
import { useClaudeAccounts } from "@/features/claude/hooks/use-claude-accounts";
import { getErrorMessageOrNull } from "@/utils/errors";

export function ClaudeAccountsPage() {
  const { t } = useTranslation();
  const canWrite = useAuthStore((state) => state.canWrite);
  const {
    accountsQuery,
    addMutation,
    disableMutation,
    enableMutation,
  } = useClaudeAccounts();
  const addDialog = useDialogState();
  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null);

  const accounts = accountsQuery.data ?? [];
  const selectedAccount = selectedAccountId
    ? accounts.find((account) => account.id === selectedAccountId) ?? null
    : accounts[0] ?? null;

  const busy =
    addMutation.isPending ||
    disableMutation.isPending ||
    enableMutation.isPending;

  const mutationError =
    getErrorMessageOrNull(addMutation.error) ||
    getErrorMessageOrNull(disableMutation.error) ||
    getErrorMessageOrNull(enableMutation.error);

  return (
    <div className="animate-fade-in-up space-y-6" data-testid="claude-accounts-page">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("claude.tabTitle")}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t("claude.addDialog.description")}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          className="h-8 text-xs"
          onClick={() => addDialog.show()}
          disabled={!canWrite || busy}
          data-testid="add-claude-account-button"
        >
          <Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
          {t("claude.addButton")}
        </Button>
      </div>

      {mutationError ? (
        <AlertMessage variant="error">{mutationError}</AlertMessage>
      ) : null}

      {accountsQuery.isLoading ? (
        <div className="rounded-md border p-6 text-sm text-muted-foreground">
          {t("common.loading")}
        </div>
      ) : (
        <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div className="min-w-0 rounded-xl border bg-card p-3 sm:p-4">
            <ClaudeAccountList
              accounts={accounts}
              busy={busy}
              onDisable={(accountId) => {
                setSelectedAccountId(accountId);
                void disableMutation.mutateAsync({ accountId });
              }}
              onEnable={(accountId) => {
                setSelectedAccountId(accountId);
                void enableMutation.mutateAsync(accountId);
              }}
            />
          </div>
          <div className="min-w-0">
            {selectedAccount ? (
              <ClaudeAccountUsageCard account={selectedAccount} />
            ) : (
              <div className="rounded-xl border bg-card p-6 text-sm text-muted-foreground">
                {t("claude.emptyState.title")}
              </div>
            )}
          </div>
        </div>
      )}

      <AddClaudeAccountDialog
        open={addDialog.open}
        busy={addMutation.isPending}
        errorMessage={getErrorMessageOrNull(addMutation.error)}
        onOpenChange={addDialog.onOpenChange}
        onSubmit={async (payload) => {
          await addMutation.mutateAsync(payload);
        }}
      />
    </div>
  );
}