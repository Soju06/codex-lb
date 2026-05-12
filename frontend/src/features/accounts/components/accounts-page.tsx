import { Suspense, lazy, useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { AlertMessage } from "@/components/alert-message";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { useDialogState } from "@/hooks/use-dialog-state";
import { AccountDetail } from "@/features/accounts/components/account-detail";
import { AccountList } from "@/features/accounts/components/account-list";
import { AccountsSkeleton } from "@/features/accounts/components/accounts-skeleton";
import { ImportDialog } from "@/features/accounts/components/import-dialog";
import { useAccountRoutingPolicyMutation, useAccounts } from "@/features/accounts/hooks/use-accounts";
import { sortAccountsForDisplay } from "@/features/accounts/sorting";
import { useOauth } from "@/features/accounts/hooks/use-oauth";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { buildDuplicateAccountIdSet } from "@/utils/account-identifiers";
import { getErrorMessageOrNull } from "@/utils/errors";

const OauthDialog = lazy(() =>
  import("@/features/accounts/components/oauth-dialog").then((m) => ({ default: m.OauthDialog })),
);

export function AccountsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    accountsQuery,
    importMutation,
    pauseMutation,
    resumeMutation,
    deleteMutation,
  } = useAccounts();
  const routingPolicyMutation = useAccountRoutingPolicyMutation();
  const oauth = useOauth();

  const importDialog = useDialogState();
  const oauthDialog = useDialogState();
  const deleteDialog = useDialogState<string>();

  const accounts = useMemo(() => accountsQuery.data ?? [], [accountsQuery.data]);
  const quotaDisplay = useAccountQuotaDisplayStore((s) => s.quotaDisplay);
  const sortedAccounts = useMemo(() => sortAccountsForDisplay(accounts, quotaDisplay), [accounts, quotaDisplay]);
  const duplicateAccountIds = useMemo(() => buildDuplicateAccountIdSet(accounts), [accounts]);
  const selectedAccountId = searchParams.get("selected");

  const handleSelectAccount = useCallback((accountId: string) => {
    const nextSearchParams = new URLSearchParams(searchParams);
    nextSearchParams.set("selected", accountId);
    setSearchParams(nextSearchParams);
  }, [searchParams, setSearchParams]);

  const resolvedSelectedAccountId = useMemo(() => {
    if (accounts.length === 0) {
      return null;
    }
    if (selectedAccountId && accounts.some((account) => account.accountId === selectedAccountId)) {
      return selectedAccountId;
    }
    return sortedAccounts[0]?.accountId ?? null;
  }, [accounts, selectedAccountId, sortedAccounts]);

  const selectedAccount = useMemo(
    () =>
      resolvedSelectedAccountId
        ? accounts.find((account) => account.accountId === resolvedSelectedAccountId) ?? null
        : null,
    [accounts, resolvedSelectedAccountId],
  );

  const mutationBusy =
    routingPolicyMutation.isPending ||
    importMutation.isPending ||
    pauseMutation.isPending ||
    resumeMutation.isPending ||
    deleteMutation.isPending;

  const mutationError =
    getErrorMessageOrNull(routingPolicyMutation.error) ||
    getErrorMessageOrNull(importMutation.error) ||
    getErrorMessageOrNull(pauseMutation.error) ||
    getErrorMessageOrNull(resumeMutation.error) ||
    getErrorMessageOrNull(deleteMutation.error);

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Accounts</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage imported accounts and authentication flows.
        </p>
      </div>

      {mutationError ? <AlertMessage variant="error">{mutationError}</AlertMessage> : null}

      {!accountsQuery.data ? (
        <AccountsSkeleton />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
          <div className="rounded-xl border bg-card p-4">
            <AccountList
              accounts={accounts}
              selectedAccountId={resolvedSelectedAccountId}
              onSelect={handleSelectAccount}
              onOpenImport={() => importDialog.show()}
              onOpenOauth={() => oauthDialog.show()}
            />
          </div>

          <AccountDetail
            account={selectedAccount}
            showAccountId={selectedAccount ? duplicateAccountIds.has(selectedAccount.accountId) : false}
            busy={mutationBusy}
            onPause={(accountId) => void pauseMutation.mutateAsync(accountId)}
            onResume={(accountId) => void resumeMutation.mutateAsync(accountId)}
            onDelete={(accountId) => deleteDialog.show(accountId)}
            onRoutingPolicyChange={(accountId, routingPolicy) =>
              void routingPolicyMutation.mutateAsync({ accountId, routingPolicy })
            }
            onReauth={() => oauthDialog.show()}
          />
        </div>
      )}

      <ImportDialog
        open={importDialog.open}
        busy={importMutation.isPending}
        error={getErrorMessageOrNull(importMutation.error)}
        onOpenChange={importDialog.onOpenChange}
        onImport={async (file) => {
          await importMutation.mutateAsync(file);
        }}
      />

      <Suspense fallback={null}>
        <OauthDialog
          open={oauthDialog.open}
          state={oauth.state}
          onOpenChange={oauthDialog.onOpenChange}
          onStart={async (method) => {
            await oauth.start(method);
          }}
          onComplete={async () => {
            await oauth.complete();
            await accountsQuery.refetch();
          }}
          onManualCallback={async (callbackUrl) => {
            await oauth.manualCallback(callbackUrl);
          }}
          onReset={oauth.reset}
        />
      </Suspense>

      <ConfirmDialog
        open={deleteDialog.open}
        title="Delete account"
        description="This action removes the account from the load balancer configuration."
        confirmLabel="Delete"
        cancelLabel="Cancel"
        onOpenChange={deleteDialog.onOpenChange}
        onConfirm={() => {
          if (!deleteDialog.data) {
            return;
          }
          void deleteMutation.mutateAsync(deleteDialog.data).finally(() => {
            deleteDialog.hide();
          });
        }}
      />

      <LoadingOverlay visible={!!accountsQuery.data && mutationBusy} label="Updating accounts..." />
    </div>
  );
}
