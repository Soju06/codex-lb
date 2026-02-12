import { useMemo } from "react";

import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { Card, CardContent } from "@/components/ui/card";
import { AccountCards } from "@/features/dashboard/components/account-cards";
import { RequestFilters } from "@/features/dashboard/components/filters/request-filters";
import { RecentRequestsTable } from "@/features/dashboard/components/recent-requests-table";
import { StatsGrid } from "@/features/dashboard/components/stats-grid";
import { UsageDonuts } from "@/features/dashboard/components/usage-donuts";
import { useDashboard } from "@/features/dashboard/hooks/use-dashboard";
import { useRequestLogs } from "@/features/dashboard/hooks/use-request-logs";
import { buildDashboardView } from "@/features/dashboard/utils";
import { formatModelLabel } from "@/utils/formatters";

const MODEL_OPTION_DELIMITER = ":::";

export function DashboardPage() {
  const dashboardQuery = useDashboard();
  const { filters, logsQuery, optionsQuery, updateFilters } = useRequestLogs();

  const overview = dashboardQuery.data;
  const logPage = logsQuery.data;

  const view = useMemo(() => {
    if (!overview || !logPage) {
      return null;
    }
    return buildDashboardView(overview, logPage.requests);
  }, [overview, logPage]);

  const accountOptions = useMemo(() => {
    const labels = new Map<string, string>();
    for (const account of overview?.accounts ?? []) {
      labels.set(account.accountId, account.displayName || account.email || account.accountId);
    }
    return (optionsQuery.data?.accountIds ?? []).map((accountId) => ({
      value: accountId,
      label: labels.get(accountId) ?? accountId,
    }));
  }, [optionsQuery.data?.accountIds, overview?.accounts]);

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
        label: status,
      })),
    [optionsQuery.data?.statuses],
  );

  const errorMessage =
    (dashboardQuery.error instanceof Error && dashboardQuery.error.message) ||
    (logsQuery.error instanceof Error && logsQuery.error.message) ||
    (optionsQuery.error instanceof Error && optionsQuery.error.message) ||
    null;

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-sm text-muted-foreground">Overview, account health, and recent request logs.</p>
      </div>

      {errorMessage ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {errorMessage}
        </p>
      ) : null}

      {!view ? (
        <Card>
          <CardContent className="px-6 py-8 text-sm text-muted-foreground">
            Loading dashboard data...
          </CardContent>
        </Card>
      ) : (
        <>
          <StatsGrid stats={view.stats} />

          <UsageDonuts
            primaryItems={view.primaryUsageItems}
            secondaryItems={view.secondaryUsageItems}
            primaryTotal={overview?.summary.primaryWindow.capacityCredits ?? 0}
            secondaryTotal={overview?.summary.secondaryWindow?.capacityCredits ?? 0}
            primaryWindowMinutes={overview?.windows.primary.windowMinutes ?? null}
            secondaryWindowMinutes={overview?.windows.secondary?.windowMinutes ?? null}
          />

          <section className="space-y-2">
            <h2 className="text-sm font-semibold">Accounts</h2>
            <AccountCards accounts={overview?.accounts ?? []} />
          </section>

          <section className="space-y-2">
            <h2 className="text-sm font-semibold">Request Logs</h2>
            <RequestFilters
              filters={filters}
              accountOptions={accountOptions}
              modelOptions={modelOptions}
              statusOptions={statusOptions}
              total={logPage?.total ?? 0}
              hasMore={logPage?.hasMore ?? false}
              onSearchChange={(search) => updateFilters({ search, offset: 0 })}
              onTimeframeChange={(timeframe) => updateFilters({ timeframe, offset: 0 })}
              onAccountChange={(accountIds) => updateFilters({ accountIds, offset: 0 })}
              onModelChange={(modelOptionsSelected) =>
                updateFilters({ modelOptions: modelOptionsSelected, offset: 0 })
              }
              onStatusChange={(statuses) => updateFilters({ statuses, offset: 0 })}
              onLimitChange={(limit) => updateFilters({ limit, offset: 0 })}
              onOffsetChange={(offset) => updateFilters({ offset })}
              onReset={() =>
                updateFilters({
                  search: "",
                  timeframe: "all",
                  accountIds: [],
                  modelOptions: [],
                  statuses: [],
                  offset: 0,
                })
              }
            />
            <RecentRequestsTable requests={view.requestLogs} accounts={overview?.accounts ?? []} />
          </section>
        </>
      )}

      <LoadingOverlay visible={dashboardQuery.isFetching || logsQuery.isFetching} label="Refreshing..." />
    </section>
  );
}
