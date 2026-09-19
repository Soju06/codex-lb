import { useCallback, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { ProviderFilter } from "@/components/provider-filter";
import {
  parseProviderFilterValue,
  type ProviderFilterValue,
} from "@/components/provider-filter-options";
import { useAccountMutations } from "@/features/accounts/hooks/use-accounts";
import { AccountCards } from "@/features/dashboard/components/account-cards";
import { DashboardSkeleton } from "@/features/dashboard/components/dashboard-skeleton";
import { OverviewTimeframeSelect } from "@/features/dashboard/components/filters/overview-timeframe-select";
import { RequestFilters } from "@/features/dashboard/components/filters/request-filters";
import { RecentRequestsTable } from "@/features/dashboard/components/recent-requests-table";
import { StatsGrid } from "@/features/dashboard/components/stats-grid";
import { UsageDonuts } from "@/features/dashboard/components/usage-donuts";
import { WeeklyCreditsPaceCard } from "@/features/dashboard/components/weekly-credits-pace-card";
import {
  useDashboard,
  useDashboardProjections,
} from "@/features/dashboard/hooks/use-dashboard";
import {
  useRequestLogs,
  type RequestLogsScope,
} from "@/features/dashboard/hooks/use-request-logs";
import {
  accountMatchesProviderScope,
  buildDashboardView,
  filterOverviewByProvider,
} from "@/features/dashboard/utils";
import {
  DEFAULT_OVERVIEW_TIMEFRAME,
  parseOverviewTimeframe,
  type AccountSummary,
  type OverviewTimeframe,
  type RequestLogsResponse,
} from "@/features/dashboard/schemas";
import { useDashboardPreferencesStore } from "@/hooks/use-dashboard-preferences";
import { useThemeStore } from "@/hooks/use-theme";
import { REQUEST_STATUS_LABELS } from "@/utils/constants";
import { formatModelLabel, formatSlug } from "@/utils/formatters";

const MODEL_OPTION_DELIMITER = ":::";

/** Rendered when the provider scope matches no accounts (no fetch issued). */
const EMPTY_LOG_PAGE: RequestLogsResponse = {
  requests: [],
  total: 0,
  hasMore: false,
};

export function DashboardPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const isDark = useThemeStore((s) => s.theme === "dark");
  const showAccountBurnrate = useDashboardPreferencesStore(
    (s) => s.accountBurnrateEnabled,
  );
  const overviewTimeframe = useMemo(
    () => parseOverviewTimeframe(searchParams.get("overviewTimeframe")),
    [searchParams],
  );
  const providerFilter = useMemo(
    () => parseProviderFilterValue(searchParams.get("provider")),
    [searchParams],
  );
  const dashboardQuery = useDashboard(overviewTimeframe);
  const projectionsQuery = useDashboardProjections(
    Boolean(dashboardQuery.data),
  );
  const overview = dashboardQuery.data;

  // Provider scope for the request-log queries: paused until the overview's
  // account list is known so an unscoped page is never fetched first.
  const logsScope = useMemo<RequestLogsScope>(() => {
    if (providerFilter === "all") {
      return { kind: "none" };
    }
    if (!overview) {
      return { kind: "pending" };
    }
    return {
      kind: "accounts",
      accountIds: overview.accounts
        .filter((account) =>
          accountMatchesProviderScope(account, providerFilter),
        )
        .map((account) => account.accountId),
    };
  }, [overview, providerFilter]);

  const { filters, logsQuery, optionsQuery, scopeIsEmpty, updateFilters } =
    useRequestLogs(logsScope);
  const { resumeMutation, limitWarmupMutation } = useAccountMutations();

  const isRefreshing =
    dashboardQuery.isFetching ||
    projectionsQuery.isFetching ||
    logsQuery.isFetching;

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

  // Same URL scheme as the accounts page: `provider` param, omitted when
  // "all", written with replace so filter clicks don't pollute history.
  const handleProviderFilterChange = useCallback(
    (value: ProviderFilterValue) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (value === "all") {
            next.delete("provider");
          } else {
            next.set("provider", value);
          }
          // The log scope changes with the provider; back to the first page.
          next.delete("offset");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // Carry the active provider filter into the accounts deep link so the
  // accounts page opens consistently scoped.
  const accountDetailsHref = useCallback(
    (accountId: string) =>
      providerFilter === "all"
        ? `/accounts?selected=${accountId}`
        : `/accounts?selected=${accountId}&provider=${providerFilter}`,
    [providerFilter],
  );

  const handleAccountAction = useCallback(
    (account: AccountSummary, action: string) => {
      switch (action) {
        case "details":
          navigate(accountDetailsHref(account.accountId));
          break;
        case "resume":
          void resumeMutation.mutateAsync(account.accountId);
          break;
        case "reauth":
          navigate(accountDetailsHref(account.accountId));
          break;
        case "warmup-toggle":
          void limitWarmupMutation.mutateAsync({
            accountId: account.accountId,
            enabled: !account.limitWarmupEnabled,
          });
          break;
      }
    },
    [accountDetailsHref, limitWarmupMutation, navigate, resumeMutation],
  );

  const logPage = scopeIsEmpty ? EMPTY_LOG_PAGE : logsQuery.data;

  // Client-side provider scoping of the account-derived widgets (account
  // cards, donuts, burn projection, weekly pace). Identity when "all".
  const scopedOverview = useMemo(
    () =>
      overview ? filterOverviewByProvider(overview, providerFilter) : undefined,
    [overview, providerFilter],
  );

  const providerCounts = useMemo(() => {
    if (!overview) {
      return undefined;
    }
    const counts: Record<ProviderFilterValue, number> = {
      all: overview.accounts.length,
      openai: 0,
      anthropic: 0,
      glm: 0,
      kimi: 0,
      openrouter: 0,
    };
    for (const account of overview.accounts) {
      counts[account.provider ?? "openai"] += 1;
    }
    return counts;
  }, [overview]);

  const providerScoped = providerFilter !== "all";

  const view = useMemo(() => {
    if (!scopedOverview || !logPage) {
      return null;
    }
    return buildDashboardView(
      scopedOverview,
      logPage.requests,
      {
        isDark,
        showAccountBurnrate,
        providerScoped,
      },
      // Server projections (depletion safe lines, weekly pace) are pool-wide
      // aggregates; under a provider filter they are dropped so the weekly
      // pace rebuilds client-side from the filtered accounts.
      providerScoped ? undefined : projectionsQuery.data,
    );
  }, [
    scopedOverview,
    logPage,
    isDark,
    showAccountBurnrate,
    providerScoped,
    projectionsQuery.data,
  ]);

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
        label: option.keyPrefix
          ? `${option.name} · ${option.keyPrefix}`
          : option.name,
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
          <ProviderFilter
            value={providerFilter}
            counts={providerCounts}
            onChange={handleProviderFilterChange}
            className="w-fit shrink-0"
            aria-label="Filter dashboard by provider"
          />
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
            <RefreshCw
              className={`h-4 w-4${isRefreshing ? " animate-spin" : ""}`}
            />
          </button>
        </div>
      </div>

      {errorMessage ? (
        <AlertMessage variant="error">{errorMessage}</AlertMessage>
      ) : null}

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
                primaryTotal={
                  scopedOverview?.summary.primaryWindow.capacityCredits ?? 0
                }
                secondaryTotal={
                  scopedOverview?.summary.secondaryWindow?.capacityCredits ?? 0
                }
                primaryCenterValue={view.primaryTotal}
                secondaryCenterValue={view.secondaryTotal}
                safeLinePrimary={view.safeLinePrimary}
                safeLineSecondary={view.safeLineSecondary}
                showPrimary={providerFilter !== "openai"}
              />
              <WeeklyCreditsPaceCard pace={view.weeklyCreditPace} />
            </div>
          ) : (
            <UsageDonuts
              primaryItems={view.primaryUsageItems}
              secondaryItems={view.secondaryUsageItems}
              primaryTotal={
                scopedOverview?.summary.primaryWindow.capacityCredits ?? 0
              }
              secondaryTotal={
                scopedOverview?.summary.secondaryWindow?.capacityCredits ?? 0
              }
              primaryCenterValue={view.primaryTotal}
              secondaryCenterValue={view.secondaryTotal}
              safeLinePrimary={view.safeLinePrimary}
              safeLineSecondary={view.safeLineSecondary}
              showPrimary={providerFilter !== "openai"}
            />
          )}

          <section className="space-y-4">
            <div className="flex items-center gap-3">
              <h2 className="text-base font-semibold text-foreground">
                Accounts
              </h2>
              <div className="h-px flex-1 bg-border" />
            </div>
            <AccountCards
              accounts={scopedOverview?.accounts ?? []}
              onAction={handleAccountAction}
            />
          </section>

          <section className="space-y-4">
            <div className="flex items-center gap-3">
              <h2 className="text-base font-semibold text-foreground">
                Request Logs
              </h2>
              <div className="h-px flex-1 bg-border" />
            </div>
            <RequestFilters
              filters={filters}
              accountOptions={accountOptions}
              apiKeyOptions={apiKeyOptions}
              modelOptions={modelOptions}
              statusOptions={statusOptions}
              onSearchChange={(search) => updateFilters({ search, offset: 0 })}
              onTimeframeChange={(timeframe) =>
                updateFilters({ timeframe, offset: 0 })
              }
              onAccountChange={(accountIds) =>
                updateFilters({ accountIds, offset: 0 })
              }
              onApiKeyChange={(apiKeyIds) =>
                updateFilters({ apiKeyIds, offset: 0 })
              }
              onModelChange={(modelOptionsSelected) =>
                updateFilters({ modelOptions: modelOptionsSelected, offset: 0 })
              }
              onStatusChange={(statuses) =>
                updateFilters({ statuses, offset: 0 })
              }
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
    </div>
  );
}
