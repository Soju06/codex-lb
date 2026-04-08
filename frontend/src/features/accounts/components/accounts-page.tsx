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
import { PlatformIdentityDialog } from "@/features/accounts/components/platform-identity-dialog";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { useOauth } from "@/features/accounts/hooks/use-oauth";
import type { AccountSummary } from "@/features/accounts/schemas";
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
    createPlatformMutation,
    updatePlatformMutation,
    pauseMutation,
    resumeMutation,
    deleteMutation,
  } = useAccounts();
  const oauth = useOauth();

  const importDialog = useDialogState();
  const platformDialog = useDialogState();
  const platformEditDialog = useDialogState<AccountSummary>();
  const oauthDialog = useDialogState();
  const deleteDialog = useDialogState<string>();

  const accounts = useMemo(() => accountsQuery.data ?? [], [accountsQuery.data]);
  const platformIdentityRegistered = useMemo(
    () => accounts.some((account) => account.providerKind === "openai_platform"),
    [accounts],
  );
  const platformPrerequisiteSatisfied = useMemo(
    () =>
      accounts.some(
        (account) =>
          account.providerKind !== "openai_platform" &&
          account.status !== "paused" &&
          account.status !== "deactivated",
      ),
    [accounts],
  );
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
    return accounts[0].accountId;
  }, [accounts, selectedAccountId]);

  const selectedAccount = useMemo(
    () =>
      resolvedSelectedAccountId
        ? accounts.find((account) => account.accountId === resolvedSelectedAccountId) ?? null
        : null,
    [accounts, resolvedSelectedAccountId],
  );

  const mutationBusy =
    importMutation.isPending ||
    createPlatformMutation.isPending ||
    updatePlatformMutation.isPending ||
    pauseMutation.isPending ||
    resumeMutation.isPending ||
    deleteMutation.isPending;

  const mutationError =
    getErrorMessageOrNull(importMutation.error) ||
    getErrorMessageOrNull(createPlatformMutation.error) ||
    getErrorMessageOrNull(updatePlatformMutation.error) ||
    getErrorMessageOrNull(pauseMutation.error) ||
    getErrorMessageOrNull(resumeMutation.error) ||
    getErrorMessageOrNull(deleteMutation.error);
  const editingPlatformAccount =
    platformEditDialog.data?.providerKind === "openai_platform" ? platformEditDialog.data : null;

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Accounts</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage imported accounts, ChatGPT authentication, and OpenAI Platform API keys.
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
              platformIdentityRegistered={platformIdentityRegistered}
              platformPrerequisiteSatisfied={platformPrerequisiteSatisfied}
              onSelect={handleSelectAccount}
              onOpenImport={() => importDialog.show()}
              onOpenOauth={() => oauthDialog.show()}
              onOpenPlatform={() => platformDialog.show()}
            />
          </div>

          <AccountDetail
            account={selectedAccount}
            showAccountId={selectedAccount ? duplicateAccountIds.has(selectedAccount.accountId) : false}
            busy={mutationBusy}
            onEditPlatform={(account) => platformEditDialog.show(account)}
            onPause={(accountId) => void pauseMutation.mutateAsync(accountId)}
            onResume={(accountId) => void resumeMutation.mutateAsync(accountId)}
            onDelete={(accountId) => deleteDialog.show(accountId)}
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

      <PlatformIdentityDialog
        open={platformDialog.open}
        busy={createPlatformMutation.isPending}
        error={getErrorMessageOrNull(createPlatformMutation.error)}
        mode="create"
        prerequisiteSatisfied={platformPrerequisiteSatisfied}
        onOpenChange={platformDialog.onOpenChange}
        onSubmit={async (payload) => {
          await createPlatformMutation.mutateAsync(payload);
        }}
      />

      <PlatformIdentityDialog
        open={platformEditDialog.open}
        busy={updatePlatformMutation.isPending}
        error={getErrorMessageOrNull(updatePlatformMutation.error)}
        mode="edit"
        account={editingPlatformAccount}
        onOpenChange={platformEditDialog.onOpenChange}
        onSubmit={async (payload) => {
          if (!editingPlatformAccount) {
            return;
          }
          await updatePlatformMutation.mutateAsync({
            accountId: editingPlatformAccount.accountId,
            payload,
          });
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
