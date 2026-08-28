import { Suspense, lazy, useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
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
import { ResetCreditConfirmDialog } from "@/features/accounts/components/reset-credit-confirm-dialog";
import { AuthExportDialog } from "@/features/accounts/components/auth-export-dialog";
import {
  useAccounts,
  useAccountUsageResetCredits,
} from "@/features/accounts/hooks/use-accounts";
import {
  DEFAULT_ACCOUNT_SORT_MODE,
  sortAccountsForDisplay,
  type AccountSortMode,
} from "@/features/accounts/sorting";
import { useOauth } from "@/features/accounts/hooks/use-oauth";
import { useSettings, useUpstreamProxyAdmin } from "@/features/settings/hooks/use-settings";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import type { AccountAuthExportResponse } from "@/features/accounts/schemas";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { getErrorMessageOrNull } from "@/utils/errors";

const OauthDialog = lazy(() =>
  import("@/features/accounts/components/oauth-dialog").then((m) => ({
    default: m.OauthDialog,
  })),
);

export function AccountsPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [accountSortMode, setAccountSortMode] = useState<AccountSortMode>(DEFAULT_ACCOUNT_SORT_MODE);
  const [oauthAccountId, setOauthAccountId] = useState<string | null>(null);
  const {
    accountsQuery,
    importMutation,
    pauseMutation,
    resumeMutation,
    setAliasMutation,
    probeMutation,
    usageResetMutation,
    limitWarmupMutation,
    updateMutation,
    deleteMutation,
    routingPolicyMutation,
    exportAuthMutation,
  } = useAccounts();
  const { settingsQuery } = useSettings();
  const { upstreamProxyQuery, accountBindingMutation, testEndpointMutation } = useUpstreamProxyAdmin();
  const oauth = useOauth();
  const canWrite = useAuthStore((state) => state.canWrite);

  const importDialog = useDialogState();
  const oauthDialog = useDialogState();
  const deleteDialog = useDialogState<string>();
  type ResetCreditDialogTarget = { accountId: string; availableResetCredits: number };
  const resetCreditDialog = useDialogState<ResetCreditDialogTarget>();
  const usageResetDialog = useDialogState<string>();
  const exportDialog = useDialogState<AccountAuthExportResponse>();
  const [deleteHistory, setDeleteHistory] = useState(false);

  const accounts = useMemo(
    () => accountsQuery.data ?? [],
    [accountsQuery.data],
  );
  const showResetCreditBadges = settingsQuery.data?.showResetCreditBadges ?? true;
  const showResetCreditExpiryBadge = settingsQuery.data?.showResetCreditExpiryBadge ?? true;
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
  const resetCreditsQuery = useAccountUsageResetCredits(selectedAccount?.accountId ?? null);

  const mutationBusy =
    importMutation.isPending ||
    pauseMutation.isPending ||
    resumeMutation.isPending ||
    setAliasMutation.isPending ||
    probeMutation.isPending ||
    usageResetMutation.isPending ||
    limitWarmupMutation.isPending ||
    deleteMutation.isPending ||
    routingPolicyMutation.isPending ||
    exportAuthMutation.isPending ||
    updateMutation.isPending ||
    accountBindingMutation.isPending ||
    testEndpointMutation.isPending;

  const mutationError =
    getErrorMessageOrNull(importMutation.error) ||
    getErrorMessageOrNull(pauseMutation.error) ||
    getErrorMessageOrNull(resumeMutation.error) ||
    getErrorMessageOrNull(setAliasMutation.error) ||
    getErrorMessageOrNull(probeMutation.error) ||
    getErrorMessageOrNull(usageResetMutation.error) ||
    getErrorMessageOrNull(limitWarmupMutation.error) ||
    getErrorMessageOrNull(deleteMutation.error) ||
    getErrorMessageOrNull(routingPolicyMutation.error) ||
    getErrorMessageOrNull(exportAuthMutation.error) ||
    getErrorMessageOrNull(updateMutation.error) ||
    getErrorMessageOrNull(settingsQuery.error) ||
    getErrorMessageOrNull(upstreamProxyQuery.error) ||
    getErrorMessageOrNull(accountBindingMutation.error) ||
    getErrorMessageOrNull(testEndpointMutation.error);

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("accounts.page.title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("accounts.page.subtitle")}
        </p>
      </div>

      {mutationError ? (
        <AlertMessage variant="error">{mutationError}</AlertMessage>
      ) : null}

      {!accountsQuery.data ? (
        <AccountsSkeleton />
      ) : (
        <div
          data-testid="accounts-layout"
          className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-[minmax(18rem,22rem)_minmax(0,1fr)]"
        >
          <div
            data-testid="accounts-list-panel"
            className="min-w-0 min-h-0 self-start"
          >
            <div
              data-testid="accounts-list-card"
              className="flex min-h-0 min-w-0 flex-col rounded-xl border bg-card p-3 sm:p-4"
            >
              <AccountList
                accounts={accounts}
                selectedAccountId={resolvedSelectedAccountId}
                onSelect={handleSelectAccount}
                sortMode={accountSortMode}
                onSortModeChange={setAccountSortMode}
                showResetCreditBadges={showResetCreditBadges}
                onOpenImport={() => importDialog.show()}
                onOpenOauth={() => {
                  setOauthAccountId(null);
                  oauthDialog.show();
                }}
                readOnly={!canWrite}
              />
            </div>
          </div>

          <AccountDetail
            account={selectedAccount}
            showAccountId={selectedAccount?.isEmailDuplicate === true}
            busy={mutationBusy}
            readOnly={!canWrite}
            onPause={(accountId) => void pauseMutation.mutateAsync(accountId)}
            onResume={(accountId) => void resumeMutation.mutateAsync(accountId)}
            onProbe={(accountId) => void probeMutation.mutateAsync({ accountId })}
            onResetUsage={(accountId) => usageResetDialog.show(accountId)}
            onSetAlias={(accountId, alias) =>
              setAliasMutation.mutateAsync({ accountId, alias })
            }
            onDelete={(accountId) => deleteDialog.show(accountId)}
            onReauth={() => {
              setOauthAccountId(selectedAccount?.accountId ?? null);
              oauthDialog.show();
            }}
            onExportAuth={(accountId) => {
              void exportAuthMutation
                .mutateAsync(accountId)
                .then((result) => exportDialog.show(result))
                .catch(() => null);
            }}
            onResetCredit={(accountId) => {
              const account = accountsQuery.data?.find((item) => item.accountId === accountId);
              resetCreditDialog.show({
                accountId,
                availableResetCredits: account?.availableResetCredits ?? 0,
              });
            }}
            showResetCreditExpiryBadge={showResetCreditExpiryBadge}
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
            onProxyEndpointTest={(endpointId) => testEndpointMutation.mutateAsync(endpointId)}
            resetCredits={resetCreditsQuery.data?.rateLimitResetCredits ?? null}
            resetCreditsLoading={resetCreditsQuery.isFetching}
            resetCreditsUnavailable={!!resetCreditsQuery.error}
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
          onOpenChange={(open) => {
            oauthDialog.onOpenChange(open);
            if (!open) {
              setOauthAccountId(null);
            }
          }}
          onStart={async (method) => {
            await oauth.start(method, oauthAccountId ?? undefined);
          }}
          onComplete={async () => {
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

      {resetCreditDialog.data ? (
        <ResetCreditConfirmDialog
          open={resetCreditDialog.open}
          accountId={resetCreditDialog.data.accountId}
          summaryAvailableCount={resetCreditDialog.data.availableResetCredits}
          onOpenChange={resetCreditDialog.onOpenChange}
        />
      ) : null}

      <ConfirmDialog
        open={deleteDialog.open}
        title={t("accounts.deleteDialog.title")}
        description={t("accounts.deleteDialog.description")}
        confirmLabel={t("common.actions.delete")}
        cancelLabel={t("common.cancel")}
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
            {t("accounts.deleteDialog.deleteHistory")}
          </label>
        </div>
      </ConfirmDialog>

      <ConfirmDialog
        open={usageResetDialog.open}
        title={t("accounts.usageResetDialog.title")}
        description={t("accounts.usageResetDialog.description")}
        confirmLabel={t("common.actions.reset")}
        cancelLabel={t("common.cancel")}
        onOpenChange={usageResetDialog.onOpenChange}
        onConfirm={() => {
          if (!usageResetDialog.data) {
            return;
          }
          void usageResetMutation
            .mutateAsync({ accountId: usageResetDialog.data })
            .finally(() => {
              usageResetDialog.hide();
            });
        }}
      />

      <LoadingOverlay
        visible={!!accountsQuery.data && mutationBusy}
        label={t("accounts.page.updating")}
      />
    </div>
  );
}
