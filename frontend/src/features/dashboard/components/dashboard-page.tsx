import { useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { useAccountMutations } from "@/features/accounts/hooks/use-accounts";
import { AccountCards } from "@/features/dashboard/components/account-cards";
import { DashboardSkeleton } from "@/features/dashboard/components/dashboard-skeleton";
import { RequestFilters } from "@/features/dashboard/components/filters/request-filters";
import { RecentRequestsTable } from "@/features/dashboard/components/recent-requests-table";
import { StatsGrid } from "@/features/dashboard/components/stats-grid";
import { UsageDonuts } from "@/features/dashboard/components/usage-donuts";
import { useDashboard } from "@/features/dashboard/hooks/use-dashboard";
import { useRequestLogs } from "@/features/dashboard/hooks/use-request-logs";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { useUsers } from "@/features/users/hooks/use-users";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { buildDashboardView } from "@/features/dashboard/utils";
import type { AccountSummary } from "@/features/dashboard/schemas";
import { useThemeStore } from "@/hooks/use-theme";
import { REQUEST_STATUS_LABELS } from "@/utils/constants";
import { formatModelLabel, formatSlug } from "@/utils/formatters";

const MODEL_OPTION_DELIMITER = ":::";

export function DashboardPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isDark = useThemeStore((s) => s.theme === "dark");
  const role = useAuthStore((state) => state.user?.role);
  const isAdmin = role === "admin";
  const { filters, logsQuery, optionsQuery, updateFilters } = useRequestLogs();
  const dashboardQuery = useDashboard(filters.ownerUserId);
  const usersQuery = useUsers({}, isAdmin).usersQuery;
  const { resumeMutation } = useAccountMutations();

  const isRefreshing = dashboardQuery.isFetching || logsQuery.isFetching;

  const handleRefresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  }, [queryClient]);

  const handleAccountAction = useCallback(
    (account: AccountSummary, action: string) => {
      switch (action) {
        case "details":
          navigate(`/accounts?selected=${account.accountId}`);
          break;
        case "resume":
          void resumeMutation.mutateAsync(account.accountId);
          break;
        case "reauth":
          navigate(`/accounts?selected=${account.accountId}`);
          break;
      }
    },
    [navigate, resumeMutation],
  );

  const overview = dashboardQuery.data;
  const logPage = logsQuery.data;

  const view = useMemo(() => {
    if (!overview || !logPage) {
      return null;
    }
    return buildDashboardView(overview, logPage.requests, isDark);
  }, [overview, logPage, isDark]);

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
          {isAdmin ? (
            <Select
              value={filters.ownerUserId ?? "all"}
              onValueChange={(value) =>
                updateFilters({
                  ownerUserId: value === "all" ? undefined : value,
                  accountIds: [],
                  modelOptions: [],
                  statuses: [],
                  offset: 0,
                })
              }
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
          ) : null}
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

          <UsageDonuts
            primaryItems={view.primaryUsageItems}
            secondaryItems={view.secondaryUsageItems}
            primaryTotal={overview?.summary.primaryWindow.capacityCredits ?? 0}
            secondaryTotal={overview?.summary.secondaryWindow?.capacityCredits ?? 0}
            primaryWindowMinutes={overview?.windows.primary.windowMinutes ?? null}
            secondaryWindowMinutes={overview?.windows.secondary?.windowMinutes ?? null}
          />

          <section className="space-y-4">
            <div className="flex items-center gap-3">
              <h2 className="text-[13px] font-medium uppercase tracking-wider text-muted-foreground">Accounts</h2>
              <div className="h-px flex-1 bg-border" />
            </div>
            <AccountCards accounts={overview?.accounts ?? []} onAction={handleAccountAction} />
          </section>

          <section className="space-y-4">
            <div className="flex items-center gap-3">
              <h2 className="text-[13px] font-medium uppercase tracking-wider text-muted-foreground">Request Logs</h2>
              <div className="h-px flex-1 bg-border" />
            </div>
            <RequestFilters
              filters={filters}
              accountOptions={accountOptions}
              modelOptions={modelOptions}
              statusOptions={statusOptions}
              onSearchChange={(search) => updateFilters({ search, offset: 0 })}
              onTimeframeChange={(timeframe) => updateFilters({ timeframe, offset: 0 })}
              onAccountChange={(accountIds) => updateFilters({ accountIds, offset: 0 })}
              onModelChange={(modelOptionsSelected) =>
                updateFilters({ modelOptions: modelOptionsSelected, offset: 0 })
              }
              onStatusChange={(statuses) => updateFilters({ statuses, offset: 0 })}
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
