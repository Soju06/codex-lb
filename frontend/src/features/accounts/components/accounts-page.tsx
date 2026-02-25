import { lazy, useMemo, useState } from "react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { AlertMessage } from "@/components/alert-message";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useDialogState } from "@/hooks/use-dialog-state";
import { AccountDetail } from "@/features/accounts/components/account-detail";
import { AccountList } from "@/features/accounts/components/account-list";
import { AccountsSkeleton } from "@/features/accounts/components/accounts-skeleton";
import { ImportDialog } from "@/features/accounts/components/import-dialog";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { useOauth } from "@/features/accounts/hooks/use-oauth";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { useUsers } from "@/features/users/hooks/use-users";
import { buildDuplicateAccountIdSet } from "@/utils/account-identifiers";
import { getErrorMessageOrNull } from "@/utils/errors";

const OauthDialog = lazy(() =>
  import("@/features/accounts/components/oauth-dialog").then((m) => ({ default: m.OauthDialog })),
);

export function AccountsPage() {
  const role = useAuthStore((state) => state.user?.role);
  const isAdmin = role === "admin";
  const [ownerUserId, setOwnerUserId] = useState<string | undefined>(undefined);
  const {
    accountsQuery,
    importMutation,
    importCredentialsMutation,
    pauseMutation,
    resumeMutation,
    deleteMutation,
  } = useAccounts(ownerUserId);
  const oauth = useOauth();
  const usersQuery = useUsers({}, isAdmin).usersQuery;

  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null);
  const importDialog = useDialogState();
  const oauthDialog = useDialogState();
  const deleteDialog = useDialogState<string>();

  const accounts = useMemo(() => accountsQuery.data ?? [], [accountsQuery.data]);
  const duplicateAccountIds = useMemo(() => buildDuplicateAccountIdSet(accounts), [accounts]);

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
    importCredentialsMutation.isPending ||
    pauseMutation.isPending ||
    resumeMutation.isPending ||
    deleteMutation.isPending;

  const mutationError =
    getErrorMessageOrNull(importMutation.error) ||
    getErrorMessageOrNull(importCredentialsMutation.error) ||
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
      {isAdmin ? (
        <div className="flex justify-end">
          <Select
            value={ownerUserId ?? "all"}
            onValueChange={(value) => {
              setOwnerUserId(value === "all" ? undefined : value);
              setSelectedAccountId(null);
            }}
          >
            <SelectTrigger size="sm" className="w-56">
              <SelectValue placeholder="All users" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All users</SelectItem>
              {(usersQuery.data ?? []).map((user) => (
                <SelectItem key={user.id} value={user.id}>
                  {user.username}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}

      {mutationError ? <AlertMessage variant="error">{mutationError}</AlertMessage> : null}

      {!accountsQuery.data ? (
        <AccountsSkeleton />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
          <div className="rounded-xl border bg-card p-4">
            <AccountList
              accounts={accounts}
              selectedAccountId={resolvedSelectedAccountId}
              onSelect={setSelectedAccountId}
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
            onReauth={() => oauthDialog.show()}
          />
        </div>
      )}

      <ImportDialog
        open={importDialog.open}
        busy={importMutation.isPending || importCredentialsMutation.isPending}
        error={getErrorMessageOrNull(importMutation.error) || getErrorMessageOrNull(importCredentialsMutation.error)}
        onOpenChange={importDialog.onOpenChange}
        onImportAuthJson={async (file) => {
          await importMutation.mutateAsync(file);
        }}
        onImportCredentials={async (credentialsText) => {
          await importCredentialsMutation.mutateAsync(credentialsText);
        }}
      />

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
        onReset={oauth.reset}
      />

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
