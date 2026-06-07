import { Suspense, lazy, useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { AlertMessage } from "@/components/alert-message";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { Checkbox } from "@/components/ui/checkbox";
import { useDialogState } from "@/hooks/use-dialog-state";
import { AccountDetail } from "@/features/accounts/components/account-detail";
import { AccountList } from "@/features/accounts/components/account-list";
import { AccountsSkeleton } from "@/features/accounts/components/accounts-skeleton";
import { ImportDialog } from "@/features/accounts/components/import-dialog";
import { AuthExportDialog } from "@/features/accounts/components/auth-export-dialog";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import {
  DEFAULT_ACCOUNT_SORT_MODE,
  sortAccountsForDisplay,
  type AccountSortMode,
} from "@/features/accounts/sorting";
import { useOauth } from "@/features/accounts/hooks/use-oauth";
import { useUpstreamProxyAdmin } from "@/features/settings/hooks/use-settings";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import type { AccountAuthExportResponse } from "@/features/accounts/schemas";
import { getErrorMessageOrNull } from "@/utils/errors";

const OauthDialog = lazy(() =>
  import("@/features/accounts/components/oauth-dialog").then((m) => ({
    default: m.OauthDialog,
  })),
);

export function AccountsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [accountSortMode, setAccountSortMode] = useState<AccountSortMode>(DEFAULT_ACCOUNT_SORT_MODE);
  const {
    accountsQuery,
    importMutation,
    pauseMutation,
    resumeMutation,
    setAliasMutation,
    probeMutation,
    limitWarmupMutation,
    updateMutation,
    deleteMutation,
    routingPolicyMutation,
    exportAuthMutation,
  } = useAccounts();
  const { upstreamProxyQuery, accountBindingMutation } = useUpstreamProxyAdmin();
  const oauth = useOauth();

  const importDialog = useDialogState();
  const oauthDialog = useDialogState();
  const deleteDialog = useDialogState<string>();
  const exportDialog = useDialogState<AccountAuthExportResponse>();
  const [deleteHistory, setDeleteHistory] = useState(false);

  const accounts = useMemo(
    () => accountsQuery.data ?? [],
    [accountsQuery.data],
  );
  const quotaDisplay = useAccountQuotaDisplayStore((s) => s.quotaDisplay);
  const sortedAccounts = useMemo(
    () => sortAccountsForDisplay(accounts, quotaDisplay, accountSortMode),
    [accounts, quotaDisplay, accountSortMode],
  );
  const selectedAccountId = searchParams.get("selected");

  const handleSelectAccount = useCallback(
    (accountId: string) => {
      const nextSearchParams = new URLSearchParams(searchParams);
      nextSearchParams.set("selected", accountId);
      setSearchParams(nextSearchParams);
    },
    [searchParams, setSearchParams],
  );

  const resolvedSelectedAccountId = useMemo(() => {
    if (accounts.length === 0) {
      return null;
    }
    if (
      selectedAccountId &&
      accounts.some((account) => account.accountId === selectedAccountId)
    ) {
      return selectedAccountId;
    }
    return sortedAccounts[0]?.accountId ?? null;
  }, [accounts, selectedAccountId, sortedAccounts]);

  const selectedAccount = useMemo(
    () =>
      resolvedSelectedAccountId
        ? (accounts.find(
            (account) => account.accountId === resolvedSelectedAccountId,
          ) ?? null)
        : null,
    [accounts, resolvedSelectedAccountId],
  );

  const mutationBusy =
    importMutation.isPending ||
    pauseMutation.isPending ||
    resumeMutation.isPending ||
    setAliasMutation.isPending ||
    probeMutation.isPending ||
    limitWarmupMutation.isPending ||
    deleteMutation.isPending ||
    routingPolicyMutation.isPending ||
    exportAuthMutation.isPending ||
    updateMutation.isPending ||
    accountBindingMutation.isPending;

  const mutationError =
    getErrorMessageOrNull(importMutation.error) ||
    getErrorMessageOrNull(pauseMutation.error) ||
    getErrorMessageOrNull(resumeMutation.error) ||
    getErrorMessageOrNull(setAliasMutation.error) ||
    getErrorMessageOrNull(probeMutation.error) ||
    getErrorMessageOrNull(limitWarmupMutation.error) ||
    getErrorMessageOrNull(deleteMutation.error) ||
    getErrorMessageOrNull(routingPolicyMutation.error) ||
    getErrorMessageOrNull(exportAuthMutation.error) ||
    getErrorMessageOrNull(updateMutation.error) ||
    getErrorMessageOrNull(upstreamProxyQuery.error) ||
    getErrorMessageOrNull(accountBindingMutation.error);

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Accounts</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage imported accounts and authentication flows.
        </p>
      </div>

      {mutationError ? (
        <AlertMessage variant="error">{mutationError}</AlertMessage>
      ) : null}

      {!accountsQuery.data ? (
        <AccountsSkeleton />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
          <div className="rounded-xl border bg-card p-4">
            <AccountList
              accounts={accounts}
              selectedAccountId={resolvedSelectedAccountId}
              onSelect={handleSelectAccount}
              sortMode={accountSortMode}
              onSortModeChange={setAccountSortMode}
              onOpenImport={() => importDialog.show()}
              onOpenOauth={() => oauthDialog.show()}
            />
          </div>

          <AccountDetail
            account={selectedAccount}
            showAccountId={selectedAccount?.isEmailDuplicate === true}
            busy={mutationBusy}
            onPause={(accountId) => void pauseMutation.mutateAsync(accountId)}
            onResume={(accountId) => void resumeMutation.mutateAsync(accountId)}
            onProbe={(accountId) =>
              void probeMutation.mutateAsync({ accountId })
            }
            onSetAlias={(accountId, alias) =>
              setAliasMutation.mutateAsync({ accountId, alias })
            }
            onDelete={(accountId) => deleteDialog.show(accountId)}
            onReauth={() => oauthDialog.show()}
            onExportAuth={(accountId) => {
              void exportAuthMutation
                .mutateAsync(accountId)
                .then((result) => exportDialog.show(result))
                .catch(() => null);
            }}
            onLimitWarmupChange={(accountId, enabled) =>
              void limitWarmupMutation.mutateAsync({ accountId, enabled })
            }
            onRoutingPolicyChange={(accountId, routingPolicy) =>
              void routingPolicyMutation.mutateAsync({
                accountId,
                routingPolicy,
              })
            }
            onSecurityWorkAuthorizedChange={(accountId, enabled) =>
              void updateMutation.mutateAsync({
                accountId,
                securityWorkAuthorized: enabled,
              })
            }
            upstreamProxyAdmin={upstreamProxyQuery.data ?? null}
            onProxyBindingSave={(accountId, payload) =>
              accountBindingMutation.mutateAsync({ accountId, payload })
            }
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

      <AuthExportDialog
        open={exportDialog.open}
        exportData={exportDialog.data}
        onOpenChange={exportDialog.onOpenChange}
      />

      <ConfirmDialog
        open={deleteDialog.open}
        title="Delete account"
        description="This action removes the account from the load balancer configuration."
        confirmLabel="Delete"
        cancelLabel="Cancel"
        onOpenChange={(open) => {
          deleteDialog.onOpenChange(open);
          if (!open) setDeleteHistory(false);
        }}
        onConfirm={() => {
          if (!deleteDialog.data) {
            return;
          }
          void deleteMutation
            .mutateAsync({ accountId: deleteDialog.data, deleteHistory })
            .finally(() => {
              deleteDialog.hide();
              setDeleteHistory(false);
            });
        }}
      >
        <div className="flex items-center gap-2">
          <Checkbox
            id="delete-history"
            checked={deleteHistory}
            onCheckedChange={(checked) => setDeleteHistory(checked === true)}
          />
          <label
            htmlFor="delete-history"
            className="text-sm text-muted-foreground cursor-pointer"
          >
            Delete all history for this account
          </label>
        </div>
      </ConfirmDialog>

      <LoadingOverlay
        visible={!!accountsQuery.data && mutationBusy}
        label="Updating accounts..."
      />
    </div>
  );
}
