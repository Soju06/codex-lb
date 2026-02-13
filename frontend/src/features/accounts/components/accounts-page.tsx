import { useMemo, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { AccountDetail } from "@/features/accounts/components/account-detail";
import { AccountList } from "@/features/accounts/components/account-list";
import { ImportDialog } from "@/features/accounts/components/import-dialog";
import { OauthDialog } from "@/features/accounts/components/oauth-dialog";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { useOauth } from "@/features/accounts/hooks/use-oauth";

function getErrorMessage(error: unknown): string | null {
  if (!error) {
    return null;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Request failed";
}

export function AccountsPage() {
  const {
    accountsQuery,
    importMutation,
    pauseMutation,
    resumeMutation,
    deleteMutation,
  } = useAccounts();
  const oauth = useOauth();

  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [oauthOpen, setOauthOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const accounts = useMemo(() => accountsQuery.data ?? [], [accountsQuery.data]);

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

  const busy =
    accountsQuery.isFetching ||
    importMutation.isPending ||
    pauseMutation.isPending ||
    resumeMutation.isPending ||
    deleteMutation.isPending;

  const mutationError =
    getErrorMessage(importMutation.error) ||
    getErrorMessage(pauseMutation.error) ||
    getErrorMessage(resumeMutation.error) ||
    getErrorMessage(deleteMutation.error);

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Accounts</h1>
        <p className="text-sm text-muted-foreground">Manage imported accounts and authentication flows.</p>
      </div>

      {mutationError ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {mutationError}
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
        <div className="rounded-xl border bg-card p-4">
          <AccountList
            accounts={accounts}
            selectedAccountId={resolvedSelectedAccountId}
            onSelect={setSelectedAccountId}
            onOpenImport={() => setImportOpen(true)}
            onOpenOauth={() => setOauthOpen(true)}
          />
        </div>

        <AccountDetail
          account={selectedAccount}
          busy={busy}
          onPause={(accountId) => void pauseMutation.mutateAsync(accountId)}
          onResume={(accountId) => void resumeMutation.mutateAsync(accountId)}
          onDelete={(accountId) => setDeleteTarget(accountId)}
          onReauth={() => setOauthOpen(true)}
        />
      </div>

      <ImportDialog
        open={importOpen}
        busy={importMutation.isPending}
        error={getErrorMessage(importMutation.error)}
        onOpenChange={setImportOpen}
        onImport={async (file) => {
          await importMutation.mutateAsync(file);
        }}
      />

      <OauthDialog
        open={oauthOpen}
        state={oauth.state}
        onOpenChange={setOauthOpen}
        onStart={async (method) => {
          await oauth.start(method);
        }}
        onComplete={async () => {
          await oauth.complete();
          await accountsQuery.refetch();
        }}
        onReset={oauth.reset}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete account"
        description="This action removes the account from the load balancer configuration."
        confirmLabel="Delete"
        cancelLabel="Cancel"
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null);
          }
        }}
        onConfirm={() => {
          if (!deleteTarget) {
            return;
          }
          void deleteMutation.mutateAsync(deleteTarget).finally(() => {
            setDeleteTarget(null);
          });
        }}
      />

      <LoadingOverlay visible={busy} label="Updating accounts..." />
    </section>
  );
}
