import { useCallback, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { useDialogState } from "@/hooks/use-dialog-state";
import { useAccountMutations } from "@/features/accounts/hooks/use-accounts";
import { ResetCreditConfirmDialog } from "@/features/accounts/components/reset-credit-confirm-dialog";
import { AccountCards } from "@/features/dashboard/components/account-cards";
import { AccountList } from "@/features/dashboard/components/account-list";
import { AccountSummaryLine } from "@/features/dashboard/components/account-summary-line";
import { AccountViewModeToggle } from "@/features/dashboard/components/account-view-mode-toggle";
import { DashboardSkeleton } from "@/features/dashboard/components/dashboard-skeleton";
import { OverviewTimeframeSelect } from "@/features/dashboard/components/filters/overview-timeframe-select";
import { RequestFilters } from "@/features/dashboard/components/filters/request-filters";
import { RecentRequestsTable } from "@/features/dashboard/components/recent-requests-table";
import { StatsGrid } from "@/features/dashboard/components/stats-grid";
import { UsageDonuts } from "@/features/dashboard/components/usage-donuts";
import { WeeklyCreditsPaceCard } from "@/features/dashboard/components/weekly-credits-pace-card";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { useDashboard, useDashboardProjections } from "@/features/dashboard/hooks/use-dashboard";
import { useRequestLogs } from "@/features/dashboard/hooks/use-request-logs";
import { buildDashboardView } from "@/features/dashboard/utils";
import {
  DEFAULT_OVERVIEW_TIMEFRAME,
  parseOverviewTimeframe,
  type AccountSummary,
  type OverviewTimeframe,
} from "@/features/dashboard/schemas";
import { useDashboardPreferencesStore } from "@/hooks/use-dashboard-preferences";
import { useThemeStore } from "@/hooks/use-theme";
import { REQUEST_STATUS_LABELS } from "@/utils/constants";
import { formatModelLabel, formatSlug } from "@/utils/formatters";

const MODEL_OPTION_DELIMITER = ":::";

export function DashboardPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const isDark = useThemeStore((s) => s.theme === "dark");
  const showAccountBurnrate = useDashboardPreferencesStore((s) => s.accountBurnrateEnabled);
  const accountViewMode = useDashboardPreferencesStore((s) => s.accountViewMode);
  const accountListSort = useDashboardPreferencesStore((s) => s.accountListSort);
  const setAccountViewMode = useDashboardPreferencesStore((s) => s.setAccountViewMode);
  const setAccountListSort = useDashboardPreferencesStore((s) => s.setAccountListSort);
  const canWrite = useAuthStore((state) => state.canWrite);
  const overviewTimeframe = useMemo(
    () => parseOverviewTimeframe(searchParams.get("overviewTimeframe")),
    [searchParams],
  );
  const dashboardQuery = useDashboard(overviewTimeframe);
  const projectionsQuery = useDashboardProjections(Boolean(dashboardQuery.data));
  const { filters, logsQuery, optionsQuery, updateFilters } = useRequestLogs();
  const { resumeMutation, limitWarmupMutation } = useAccountMutations();
  type ResetCreditDialogTarget = { accountId: string; availableResetCredits: number };
  const resetCreditDialog = useDialogState<ResetCreditDialogTarget>();

  const isRefreshing = dashboardQuery.isFetching || projectionsQuery.isFetching || logsQuery.isFetching;

  const handleRefresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  }, [queryClient]);

  const handleOverviewTimeframeChange = useCallback(
    (timeframe: OverviewTimeframe) => {
      const next = new URLSearchParams(searchParams);
      if (timeframe === DEFAULT_OVERVIEW_TIMEFRAME) {
        next.delete("overviewTimeframe");
      } else {
        next.set("overviewTimeframe", timeframe);
      }
      setSearchParams(next);
    },
    [searchParams, setSearchParams],
  );

  const handleAccountAction = useCallback(
    (account: AccountSummary, action: string) => {
      switch (action) {
        case "details":
          navigate(`/accounts?selected=${account.accountId}`);
          break;
        case "resume":
          if (canWrite) {
            void resumeMutation.mutateAsync(account.accountId);
          }
          break;
        case "reauth":
          navigate(`/accounts?selected=${account.accountId}`);
          break;
        case "warmup-toggle":
          if (canWrite) {
            void limitWarmupMutation.mutateAsync({
              accountId: account.accountId,
              enabled: !account.limitWarmupEnabled,
            });
          }
          break;
        case "reset-credit":
          resetCreditDialog.show({
            accountId: account.accountId,
            availableResetCredits: account.availableResetCredits ?? 0,
          });
          break;
      }
    },
    [canWrite, limitWarmupMutation, navigate, resetCreditDialog, resumeMutation],
  );

  const overview = dashboardQuery.data;
  const logPage = logsQuery.data;

  const view = useMemo(() => {
    if (!overview || !logPage) {
      return null;
    }
    return buildDashboardView(
      overview,
      logPage.requests,
      {
        isDark,
        showAccountBurnrate,
      },
      projectionsQuery.data,
    );
  }, [overview, logPage, isDark, showAccountBurnrate, projectionsQuery.data]);

  const accountOptions = useMemo(() => {
    const entries = new Map<string, { label: string; isEmail: boolean }>();
    for (const account of overview?.accounts ?? []) {
      const raw = account.displayName || account.email || account.accountId;
      const isEmail = !!account.email && raw === account.email;
      entries.set(account.accountId, { label: raw, isEmail });
    }
    return (optionsQuery.data?.accountIds ?? []).map((accountId) => {
      const entry = entries.get(accountId);
      return {
        value: accountId,
        label: entry?.label ?? accountId,
        isEmail: entry?.isEmail ?? false,
      };
    });
  }, [optionsQuery.data?.accountIds, overview?.accounts]);

  const apiKeyOptions = useMemo(
    () =>
      (optionsQuery.data?.apiKeys ?? []).map((option) => ({
        value: option.id,
        label: option.keyPrefix ? `${option.name} · ${option.keyPrefix}` : option.name,
      })),
    [optionsQuery.data?.apiKeys],
  );

  const modelOptions = useMemo(
    () =>
      (optionsQuery.data?.modelOptions ?? []).map((option) => ({
        value: `${option.model}${MODEL_OPTION_DELIMITER}${option.reasoningEffort ?? ""}`,
        label: formatModelLabel(option.model, option.reasoningEffort),
      })),
    [optionsQuery.data?.modelOptions],
  );

  const statusOptions = useMemo(
    () =>
      (optionsQuery.data?.statuses ?? []).map((status) => ({
        value: status,
        label: REQUEST_STATUS_LABELS[status] ?? formatSlug(status),
      })),
    [optionsQuery.data?.statuses],
  );

  const errorMessage =
    (dashboardQuery.error instanceof Error && dashboardQuery.error.message) ||
    (logsQuery.error instanceof Error && logsQuery.error.message) ||
    (optionsQuery.error instanceof Error && optionsQuery.error.message) ||
    null;

  return (
    <div className="animate-fade-in-up space-y-8">
      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Overview, account health, and recent request logs.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <OverviewTimeframeSelect
            value={overviewTimeframe}
            onChange={handleOverviewTimeframeChange}
          />
          <button
            type="button"
            onClick={handleRefresh}
            disabled={isRefreshing}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50"
            title="Refresh dashboard"
          >
            <RefreshCw className={`h-4 w-4${isRefreshing ? " animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {errorMessage ? <AlertMessage variant="error">{errorMessage}</AlertMessage> : null}

      {!view ? (
        <DashboardSkeleton />
      ) : (
        <>
          <StatsGrid stats={view.stats} />

          {view.weeklyCreditPace ? (
            <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
              <UsageDonuts
                primaryItems={view.primaryUsageItems}
                secondaryItems={view.secondaryUsageItems}
                primaryTotal={overview?.summary.primaryWindow.capacityCredits ?? 0}
                secondaryTotal={overview?.summary.secondaryWindow?.capacityCredits ?? 0}
                primaryCenterValue={view.primaryTotal}
                secondaryCenterValue={view.secondaryTotal}
                safeLinePrimary={view.safeLinePrimary}
                safeLineSecondary={view.safeLineSecondary}
              />
              <WeeklyCreditsPaceCard pace={view.weeklyCreditPace} />
            </div>
          ) : (
            <UsageDonuts
              primaryItems={view.primaryUsageItems}
              secondaryItems={view.secondaryUsageItems}
              primaryTotal={overview?.summary.primaryWindow.capacityCredits ?? 0}
              secondaryTotal={overview?.summary.secondaryWindow?.capacityCredits ?? 0}
              primaryCenterValue={view.primaryTotal}
              secondaryCenterValue={view.secondaryTotal}
              safeLinePrimary={view.safeLinePrimary}
              safeLineSecondary={view.safeLineSecondary}
            />
          )}

          <section className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex min-w-0 items-center gap-3">
                <h2 className="text-[13px] font-medium uppercase tracking-wider text-muted-foreground">Accounts</h2>
                <AccountSummaryLine accounts={overview?.accounts ?? []} />
              </div>
              <div className="h-px min-w-8 flex-1 bg-border" />
              <AccountViewModeToggle value={accountViewMode} onChange={setAccountViewMode} />
            </div>
            {accountViewMode === "list" ? (
              <AccountList
                accounts={overview?.accounts ?? []}
                readOnly={!canWrite}
                sort={accountListSort}
                onSortChange={setAccountListSort}
                onAction={handleAccountAction}
              />
            ) : (
              <AccountCards accounts={overview?.accounts ?? []} readOnly={!canWrite} onAction={handleAccountAction} />
            )}
          </section>

          <section className="space-y-4">
            <div className="flex items-center gap-3">
              <h2 className="text-[13px] font-medium uppercase tracking-wider text-muted-foreground">Request Logs</h2>
              <div className="h-px flex-1 bg-border" />
            </div>
            <RequestFilters
              filters={filters}
              accountOptions={accountOptions}
              apiKeyOptions={apiKeyOptions}
              modelOptions={modelOptions}
              statusOptions={statusOptions}
              onSearchChange={(search) => updateFilters({ search, offset: 0 })}
              onTimeframeChange={(timeframe) => updateFilters({ timeframe, offset: 0 })}
              onAccountChange={(accountIds) => updateFilters({ accountIds, offset: 0 })}
              onApiKeyChange={(apiKeyIds) => updateFilters({ apiKeyIds, offset: 0 })}
              onModelChange={(modelOptionsSelected) =>
                updateFilters({ modelOptions: modelOptionsSelected, offset: 0 })
              }
              onStatusChange={(statuses) => updateFilters({ statuses, offset: 0 })}
              onReset={() =>
                updateFilters({
                  search: "",
                  timeframe: "all",
                  accountIds: [],
                  apiKeyIds: [],
                  modelOptions: [],
                  statuses: [],
                  offset: 0,
                })
              }
            />
            <div className="transition-opacity duration-200">
              <RecentRequestsTable
                requests={view.requestLogs}
                accounts={overview?.accounts ?? []}
                total={logPage?.total ?? 0}
                limit={filters.limit}
                offset={filters.offset}
                hasMore={logPage?.hasMore ?? false}
                onLimitChange={(limit) => updateFilters({ limit, offset: 0 })}
                onOffsetChange={(offset) => updateFilters({ offset })}
              />
            </div>
          </section>
        </>
      )}

      {resetCreditDialog.data ? (
        <ResetCreditConfirmDialog
          open={resetCreditDialog.open}
          accountId={resetCreditDialog.data.accountId}
          summaryAvailableCount={resetCreditDialog.data.availableResetCredits}
          onOpenChange={resetCreditDialog.onOpenChange}
        />
      ) : null}

    </div>
  );
}
