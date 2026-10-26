import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Users } from "lucide-react";
import { lazy, Suspense, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router-dom";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "@/components/lazy-recharts";
import type { SparklineChartProps } from "@/components/sparkline-chart";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { OVERVIEW_STATUS_ORDER, summarizeAccountsByPlan, type WindowOverview } from "@/features/accounts/overview";
import { getRequestLogs } from "@/features/dashboard/api";
import { DashboardViewTabs } from "@/features/dashboard/components/dashboard-view-tabs";
import { RecentRequestsTable } from "@/features/dashboard/components/recent-requests-table";
import { useDashboard } from "@/features/dashboard/hooks/use-dashboard";
import { useRequestLogTablePreferences } from "@/features/dashboard/hooks/use-request-log-table-preferences";
import {
  buildActivitySeries,
  buildQuotaColumns,
  REQUEST_STATUS_FILTERS,
  summarizeRequestLogs,
  type ActivityPoint,
  type RequestStatusFilter,
} from "@/features/dashboard/overview";
import {
  DEFAULT_OVERVIEW_TIMEFRAME,
  parseOverviewTimeframe,
  type AccountSummary,
  type OverviewTimeframe,
} from "@/features/dashboard/schemas";
import { buildDashboardView, formatCompactElapsed, type DashboardStat } from "@/features/dashboard/utils";
import { useDashboardPreferencesStore } from "@/hooks/use-dashboard-preferences";
import { useThemeStore } from "@/hooks/use-theme";
import { cn } from "@/lib/utils";
import { normalizeStatus, quotaBarColor, type DashboardAccountStatus } from "@/utils/account-status";
import { getErrorMessageOrNull } from "@/utils/errors";
import {
  formatChartDateTime,
  formatCompactNumber,
  formatPercentNullable,
  formatQuotaResetLabel,
  formatRate,
  formatSlug,
} from "@/utils/formatters";

const SparklineChart = lazy(() =>
  import("@/components/sparkline-chart").then((module) => ({
    default: (props: SparklineChartProps) => <module.SparklineChart {...props} />,
  })),
);

const TIMEFRAMES: { value: OverviewTimeframe; label: string }[] = [
  { value: "1d", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
];
// Same first page as the Dashboard request log.
const REQUEST_PAGE_SIZE = 25;
const LARGE_GROUP_SIZE = 100;
const ALL_PLANS = "__all__";

// Same hues as StatusBadge, as solid fills.
const STATUS_FILL: Record<DashboardAccountStatus, string> = {
  active: "bg-emerald-500",
  paused: "bg-amber-500",
  limited: "bg-orange-500",
  exceeded: "bg-red-500",
  reauth: "bg-sky-500",
  deactivated: "bg-zinc-400",
};

type ChartMetric = "requests" | "tokens" | "errorRate";
const CHART_SERIES: Record<ChartMetric, { key: keyof ActivityPoint; color: string; labelKey: string }[]> = {
  requests: [
    { key: "requests", color: "#3b82f6", labelKey: "dashboard.overview.chart.requests" },
    { key: "errors", color: "#ef4444", labelKey: "dashboard.overview.chart.errors" },
  ],
  tokens: [{ key: "tokens", color: "#8b5cf6", labelKey: "dashboard.overview.chart.tokens" }],
  errorRate: [{ key: "errorRate", color: "#f59e0b", labelKey: "dashboard.overview.chart.errorRate" }],
};

export function DashboardOverviewPage() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const timeframe = parseOverviewTimeframe(searchParams.get("overviewTimeframe"));
  const dashboardQuery = useDashboard(timeframe);
  const isDark = useThemeStore((state) => state.theme === "dark");
  const overview = dashboardQuery.data;
  // The stat labels are translated inside the builder; the language is part of the input.
  void i18n.resolvedLanguage;
  const stats = overview ? buildDashboardView(overview, [], { isDark, showAccountBurnrate: false }).stats : null;
  const error = getErrorMessageOrNull(dashboardQuery.error);

  const handleTimeframeChange = (next: OverviewTimeframe) => {
    const params = new URLSearchParams(searchParams);
    if (next === DEFAULT_OVERVIEW_TIMEFRAME) {
      params.delete("overviewTimeframe");
    } else {
      params.set("overviewTimeframe", next);
    }
    setSearchParams(params);
  };

  return (
    <div className="animate-fade-in-up space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("dashboard.overview.title")}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t("dashboard.overview.subtitle")}</p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <DashboardViewTabs />
          <Segmented
            label={t("dashboard.overview.timeframe")}
            value={timeframe}
            options={TIMEFRAMES}
            onChange={handleTimeframeChange}
          />
          <button
            type="button"
            onClick={() => void queryClient.invalidateQueries({ queryKey: ["dashboard"] })}
            disabled={dashboardQuery.isFetching}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50"
            aria-label={t("dashboard.page.refresh")}
            title={t("dashboard.page.refresh")}
          >
            <RefreshCw className={cn("h-4 w-4", dashboardQuery.isFetching && "animate-spin")} aria-hidden="true" />
          </button>
        </div>
      </div>

      {error && !overview ? (
        <div className="space-y-3 rounded-xl border bg-card p-4">
          <div role="alert">
            <AlertMessage variant="error">{error}</AlertMessage>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={() => void dashboardQuery.refetch()} disabled={dashboardQuery.isFetching}>
            {t("common.actions.retry")}
          </Button>
        </div>
      ) : !overview || !stats ? (
        <div data-testid="dashboard-overview-loading" className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {[0, 1, 2, 3].map((index) => (
              <Skeleton key={index} className="h-40 rounded-2xl" />
            ))}
          </div>
          <Skeleton className="h-72 rounded-2xl" />
        </div>
      ) : (
        <>
          {error ? (
            // A failed poll keeps the last data on screen; say it is stale instead of passing it off as current.
            <div role="alert" className="flex flex-wrap items-center gap-3">
              <AlertMessage variant="error">{error}</AlertMessage>
              <Button type="button" variant="outline" size="sm" onClick={() => void dashboardQuery.refetch()} disabled={dashboardQuery.isFetching}>
                {t("common.actions.retry")}
              </Button>
            </div>
          ) : null}
          <section className="space-y-4" aria-labelledby="overview-activity">
            <SectionTitle id="overview-activity">{t("dashboard.overview.activityTitle")}</SectionTitle>
            <div className={cn("grid gap-3 sm:grid-cols-2", stats.length >= 5 ? "xl:grid-cols-5" : "xl:grid-cols-4")}>
              {stats.map((stat, index) => (
                <StatCard key={stat.label} stat={stat} index={index} />
              ))}
            </div>
            <ActivityChart
              points={buildActivitySeries(overview.trends)}
              bucketSeconds={overview.timeframe.bucketSeconds}
              windowMinutes={overview.timeframe.windowMinutes}
            />
          </section>

          <AccountsSection accounts={overview.accounts} />
        </>
      )}

      <RequestsSection accounts={overview?.accounts ?? []} />
    </div>
  );
}

function SectionTitle({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="text-[13px] font-medium uppercase tracking-wider text-muted-foreground">
      {children}
    </h2>
  );
}

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex h-8 items-center rounded-md border bg-background p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
          className={cn(
            "inline-flex h-6 items-center rounded-[5px] px-2.5 text-xs font-medium text-muted-foreground transition-colors",
            option.value === value ? "bg-accent text-accent-foreground shadow-sm" : "hover:bg-accent/60 hover:text-accent-foreground",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

const COMPARISON_STYLES = {
  positive: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  negative: "bg-red-500/10 text-red-600 dark:text-red-400",
  neutral: "bg-muted text-muted-foreground",
} as const;

function StatCard({ stat, index }: { stat: DashboardStat; index: number }) {
  return (
    <div className="flex min-w-0 flex-col rounded-2xl border bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-medium text-muted-foreground">{stat.label}</span>
        {stat.comparison ? (
          <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums", COMPARISON_STYLES[stat.comparison.tone])}>
            {stat.comparison.text}
          </span>
        ) : null}
      </div>
      <p className="mt-2 truncate text-3xl font-semibold tracking-tight tabular-nums" style={{ color: stat.trendColor }}>
        {stat.value}
      </p>
      {stat.meta ? <p className="mt-1 truncate text-xs text-muted-foreground">{stat.meta}</p> : null}
      {stat.trend.length > 0 ? (
        <div className="mt-auto pt-2">
          <Suspense fallback={<div style={{ height: 40 }} />}>
            <SparklineChart data={stat.trend} color={stat.trendColor} index={index} />
          </Suspense>
        </div>
      ) : null}
    </div>
  );
}

function ActivityChart({ points, bucketSeconds, windowMinutes }: { points: ActivityPoint[]; bucketSeconds: number; windowMinutes: number }) {
  const { t } = useTranslation();
  const [metric, setMetric] = useState<ChartMetric>("requests");
  const series = CHART_SERIES[metric];
  const formatValue = (value: number) => (metric === "errorRate" ? formatRate(value) : formatCompactNumber(value));
  const formatTick = (iso: string) => {
    const date = new Date(iso);
    if (windowMinutes <= 1_440) {
      return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    return bucketSeconds < 86_400
      ? date.toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit" })
      : date.toLocaleDateString([], { month: "2-digit", day: "2-digit" });
  };

  return (
    <div className="rounded-2xl border bg-card p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold">{t("dashboard.overview.chart.title")}</h3>
          <p className="text-xs text-muted-foreground">{t("dashboard.overview.chart.subtitle")}</p>
        </div>
        <Segmented
          label={t("dashboard.overview.chart.metric")}
          value={metric}
          options={[
            { value: "requests", label: t("dashboard.overview.chart.requests") },
            { value: "tokens", label: t("dashboard.overview.chart.tokens") },
            { value: "errorRate", label: t("dashboard.overview.chart.errorRate") },
          ]}
          onChange={setMetric}
        />
      </div>
      <ul className="mt-3 flex gap-4 text-xs">
        {series.map((item) => (
          <li key={item.key} className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: item.color }} aria-hidden="true" />
            {t(item.labelKey)}
          </li>
        ))}
      </ul>
      <div className="mt-2 h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
            <XAxis dataKey="t" tickFormatter={formatTick} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" minTickGap={32} />
            <YAxis tickFormatter={formatValue} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" width={48} />
            <Tooltip
              labelFormatter={(label: string) => formatChartDateTime(label)}
              formatter={(value: number, name: string) => [formatValue(value), t(series.find((item) => item.key === name)?.labelKey ?? name)]}
              contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
            />
            {series.map((item) => (
              <Area
                key={item.key}
                type="monotone"
                dataKey={item.key}
                stroke={item.color}
                strokeWidth={2}
                fill={item.color}
                fillOpacity={0.12}
                isAnimationActive={false}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function AccountsSection({ accounts }: { accounts: AccountSummary[] }) {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  // Not memoized: the next reset must move past resets that elapsed since the last poll.
  const groups = summarizeAccountsByPlan(accounts);
  const requestedPlan = searchParams.get("plan");
  // A plan that disappeared from the pool falls back to all plans.
  const group = groups.find((candidate) => candidate.plan === requestedPlan) ?? groups[0];
  const groupAccounts = group?.plan == null ? accounts : accounts.filter((account) => account.planType === group.plan);
  // Per-account marks stop being readable in large pools (thousands of free accounts); those show distributions.
  const distribution = (group?.total ?? 0) > LARGE_GROUP_SIZE;

  const handlePlanChange = (value: string) => {
    const params = new URLSearchParams(searchParams);
    if (value === ALL_PLANS) {
      params.delete("plan");
    } else {
      params.set("plan", value);
    }
    setSearchParams(params, { replace: true });
  };

  return (
    <section className="space-y-4" aria-labelledby="overview-accounts">
      <div className="flex flex-wrap items-center gap-3">
        <SectionTitle id="overview-accounts">{t("dashboard.overview.accounts.title")}</SectionTitle>
        <div className="h-px min-w-8 flex-1 bg-border" />
        {groups.length > 0 ? (
          <Select value={group?.plan ?? ALL_PLANS} onValueChange={handlePlanChange}>
            <SelectTrigger size="sm" className="w-48" aria-label={t("dashboard.overview.accounts.plan")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end">
              {groups.map((candidate) => (
                <SelectItem key={candidate.plan ?? ALL_PLANS} value={candidate.plan ?? ALL_PLANS}>
                  {candidate.plan === null ? t("dashboard.overview.accounts.allPlans") : formatSlug(candidate.plan)} · {candidate.total}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
      </div>
      {!group ? (
        <EmptyState icon={Users} title={t("accounts.list.emptyTitle")} description={t("accounts.list.emptyDescription")} />
      ) : (
        <div className={cn("grid gap-3", group.windows.length >= 3 ? "lg:grid-cols-2 xl:grid-cols-4" : "lg:grid-cols-3")}>
          <StatusTile accounts={groupAccounts} counts={group.statusCounts} total={group.total} distribution={distribution} />
          {group.windows.map((window) => (
            <QuotaTile key={window.key} window={window} accounts={groupAccounts} distribution={distribution} />
          ))}
        </div>
      )}
    </section>
  );
}

function StatusTile({
  accounts,
  counts,
  total,
  distribution,
}: {
  accounts: AccountSummary[];
  counts: Record<DashboardAccountStatus, number>;
  total: number;
  distribution: boolean;
}) {
  const { t } = useTranslation();
  const present = OVERVIEW_STATUS_ORDER.filter((status) => counts[status] > 0);
  // Grouped by status so each color reads as one block.
  const marks = distribution
    ? []
    : accounts
        .map((account) => ({ id: account.accountId, status: normalizeStatus(account.status) }))
        .sort((a, b) => OVERVIEW_STATUS_ORDER.indexOf(a.status) - OVERVIEW_STATUS_ORDER.indexOf(b.status));

  return (
    <div className="min-w-0 rounded-2xl border bg-card p-4 sm:p-5">
      <p className="text-sm font-medium text-muted-foreground">{t("dashboard.overview.accounts.status")}</p>
      <p className="mt-2 flex items-baseline gap-1.5">
        <span className="text-4xl font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">{counts.active}</span>
        <span className="text-sm text-muted-foreground">{t("dashboard.overview.accounts.activeOf", { total })}</span>
      </p>
      {/* The legend carries the same counts as text. */}
      {distribution ? (
        <div data-testid="status-distribution" className="mt-4 flex h-3.5 overflow-hidden rounded-[3px] bg-muted" aria-hidden="true">
          {present.map((status) => (
            <span key={status} className={STATUS_FILL[status]} style={{ width: `${(counts[status] / total) * 100}%` }} />
          ))}
        </div>
      ) : (
        <div className="mt-4 flex flex-wrap gap-1" aria-hidden="true">
          {marks.map((mark) => (
            <span key={mark.id} title={t(`common.status.${mark.status}`)} className={cn("h-3.5 w-3.5 rounded-[3px]", STATUS_FILL[mark.status])} />
          ))}
        </div>
      )}
      <ul className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {present.map((status) => (
          <li key={status} className="flex items-center gap-1.5">
            <span className={cn("h-2 w-2 rounded-sm", STATUS_FILL[status])} aria-hidden="true" />
            <span className="text-muted-foreground">{t(`common.status.${status}`)}</span>
            <span className="font-semibold tabular-nums">{counts[status]}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function QuotaTile({
  window,
  accounts,
  distribution,
}: {
  window: WindowOverview;
  accounts: AccountSummary[];
  distribution: boolean;
}) {
  const { t } = useTranslation();
  const label = window.key === "primary" ? t("dashboard.overview.accounts.primary") : t(window.key === "secondary" ? "common.quota.weekly" : "common.quota.monthly");
  const avgUsed = window.avgRemaining === null ? null : 100 - window.avgRemaining;
  const average = formatPercentNullable(avgUsed);
  const summary =
    avgUsed === null
      ? t("dashboard.overview.accounts.columnsNoData", { window: label })
      : t("dashboard.overview.accounts.columnsSummary", { window: label, average, known: window.known });

  return (
    <div className="min-w-0 rounded-2xl border bg-card p-4 sm:p-5">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        <p className="text-xs text-muted-foreground">
          {distribution ? t("dashboard.overview.accounts.byUsedPercent") : t("dashboard.overview.accounts.perAccount")}
        </p>
      </div>
      <p className="mt-2 flex items-baseline gap-1.5">
        <span
          className={cn(
            "text-4xl font-semibold tabular-nums",
            window.avgRemaining === null
              ? "text-muted-foreground"
              : window.avgRemaining >= 70
                ? "text-sky-500 dark:text-sky-400"
                : window.avgRemaining >= 30
                  ? "text-amber-600 dark:text-amber-400"
                  : "text-red-600 dark:text-red-400",
          )}
        >
          {avgUsed === null ? "–" : average}
        </span>
        <span className="text-sm text-muted-foreground">
          {avgUsed === null ? t("dashboard.overview.accounts.noData") : t("dashboard.overview.accounts.avgUsed")}
        </span>
      </p>
      {distribution ? <UsedHistogram window={window} summary={summary} /> : <AccountColumns accounts={accounts} window={window} summary={summary} />}
      <p className="mt-3 flex flex-wrap gap-x-2 text-xs text-muted-foreground">
        <span>
          {window.known === window.reporting
            ? t("dashboard.overview.accounts.allHaveData")
            : t("dashboard.overview.accounts.withData", { known: window.known, total: window.reporting })}
        </span>
        {distribution ? (
          <span className={cn(window.lowCount > 0 && "font-medium text-red-600 dark:text-red-400")}>
            · {t("dashboard.overview.accounts.lowCount", { count: window.lowCount })}
          </span>
        ) : null}
        {window.nextResetAt ? (
          <span>· {t("dashboard.overview.accounts.nextReset", { label: formatQuotaResetLabel(window.nextResetAt) })}</span>
        ) : null}
      </p>
    </div>
  );
}

function AccountColumns({ accounts, window, summary }: { accounts: AccountSummary[]; window: WindowOverview; summary: string }) {
  return (
    <div role="img" aria-label={summary} className="mt-4 flex h-24 items-end gap-px">
      {buildQuotaColumns(accounts, window.key).map((column) => (
        <div
          key={column.accountId}
          title={formatPercentNullable(column.usedPercent)}
          className="flex h-full min-w-[2px] flex-1 items-end rounded-[2px] bg-muted"
        >
          <div className={cn("w-full rounded-[2px]", quotaBarColor(column.remainingPercent))} style={{ height: `${column.filled * 10}%` }} />
        </div>
      ))}
    </div>
  );
}

/** Ten buckets of used percent (0–10 … 90–100), so the tile looks the same at 100 or 5,000 accounts. */
function UsedHistogram({ window, summary }: { window: WindowOverview; summary: string }) {
  const { t } = useTranslation();
  const bucketsId = useId();
  // `bins` counts remaining percent; reversed, bucket i holds roughly 10·i–10·(i+1) percent used.
  const buckets = [...window.bins].reverse().map((count, index) => ({
    from: index * 10,
    count,
    title: t("dashboard.overview.accounts.binTitle", { from: index * 10, to: index * 10 + 10, count }),
  }));
  const tallest = Math.max(1, ...window.bins);

  return (
    <>
      <div role="img" aria-label={summary} aria-describedby={bucketsId} className="mt-4 flex h-24 items-end gap-1">
        {buckets.map((bucket) => (
          <div key={bucket.from} title={bucket.title} className="flex h-full flex-1 items-end rounded-[3px] bg-muted">
            <div
              className={cn("w-full rounded-[3px]", quotaBarColor(100 - bucket.from - 5))}
              style={{ height: `${(bucket.count / tallest) * 100}%` }}
            />
          </div>
        ))}
      </div>
      <p id={bucketsId} className="sr-only">
        {buckets.map((bucket) => bucket.title).join(", ")}
      </p>
      <div className="mt-1 flex justify-between text-[10px] tabular-nums text-muted-foreground">
        <span>0%</span>
        <span>100%</span>
      </div>
    </>
  );
}

function RequestsSection({ accounts }: { accounts: AccountSummary[] }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const switchId = useId();
  const refreshSeconds = useDashboardPreferencesStore((state) => state.refreshSeconds);
  const { visibleColumns, columnWidths, setColumnWidth } = useRequestLogTablePreferences();
  const [statusFilter, setStatusFilter] = useState<RequestStatusFilter>("all");
  const [limit, setLimit] = useState(REQUEST_PAGE_SIZE);
  const [offset, setOffset] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const logsQuery = useQuery({
    queryKey: ["dashboard", "overview-requests", statusFilter, limit, offset],
    queryFn: () => getRequestLogs({ limit, offset, statuses: [...REQUEST_STATUS_FILTERS[statusFilter]] }),
    refetchInterval: autoRefresh ? refreshSeconds * 1_000 : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: autoRefresh,
    placeholderData: keepPreviousData,
  });
  const page = logsQuery.data;
  const summary = summarizeRequestLogs(page?.requests ?? []);

  const handleFilterChange = (next: RequestStatusFilter) => {
    setStatusFilter(next);
    setOffset(0);
  };

  return (
    <section className="space-y-4" aria-labelledby="overview-requests">
      <div className="flex flex-wrap items-center gap-3">
        <SectionTitle id="overview-requests">{t("dashboard.overview.requests.title")}</SectionTitle>
        <div className="h-px min-w-8 flex-1 bg-border" />
        <Segmented
          label={t("dashboard.overview.requests.filter")}
          value={statusFilter}
          options={[
            { value: "all", label: t("dashboard.overview.requests.all") },
            { value: "success", label: t("dashboard.overview.requests.success") },
            { value: "errors", label: t("dashboard.overview.requests.errors") },
          ]}
          onChange={handleFilterChange}
        />
        <div className="flex items-center gap-2">
          <Switch id={switchId} checked={autoRefresh} onCheckedChange={setAutoRefresh} />
          <Label htmlFor={switchId} className="text-xs font-medium">
            {t("dashboard.overview.requests.autoRefresh")}
          </Label>
        </div>
      </div>

      {logsQuery.error ? (
        <div role="alert" className="flex flex-wrap items-center gap-3">
          <AlertMessage variant="error">{logsQuery.error.message}</AlertMessage>
          <Button type="button" variant="outline" size="sm" onClick={() => void logsQuery.refetch()} disabled={logsQuery.isFetching}>
            {t("common.actions.retry")}
          </Button>
        </div>
      ) : null}

      {page ? (
        <>
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
            <SummaryItem label={t("dashboard.overview.requests.rows")} value={String(summary.count)} />
            <SummaryItem
              label={t("dashboard.overview.requests.errorRows")}
              value={summary.errorShare === null ? "–" : `${summary.errorCount} (${formatRate(summary.errorShare)})`}
              tone={summary.errorCount > 0 ? "text-red-600 dark:text-red-400" : undefined}
            />
            <SummaryItem label={t("dashboard.overview.requests.avgLatency")} value={formatCompactElapsed(roundOrNull(summary.avgLatencyMs)) ?? "–"} />
            <SummaryItem label={t("dashboard.overview.requests.p95Latency")} value={formatCompactElapsed(summary.p95LatencyMs) ?? "–"} />
            <SummaryItem
              label={t("dashboard.overview.requests.tokens")}
              value={`${summary.inputTokens === null ? "–" : formatCompactNumber(summary.inputTokens)} → ${summary.outputTokens === null ? "–" : formatCompactNumber(summary.outputTokens)}`}
            />
            <SummaryItem label={t("dashboard.overview.requests.cache")} value={summary.cacheShare === null ? "–" : formatRate(summary.cacheShare)} />
          </dl>
          <RecentRequestsTable
            requests={page.requests}
            accounts={accounts}
            total={page.total}
            limit={limit}
            offset={offset}
            hasMore={page.hasMore}
            filtersApplied={statusFilter !== "all"}
            visibleColumns={visibleColumns}
            columnWidths={columnWidths}
            onColumnWidthChange={setColumnWidth}
            onLimitChange={(next) => {
              setLimit(next);
              setOffset(0);
            }}
            onOffsetChange={setOffset}
            onConversationClick={(conversationId) => navigate(`/dashboard?${new URLSearchParams({ conversationId })}`)}
          />
        </>
      ) : logsQuery.isPending ? (
        <Skeleton className="h-64 rounded-2xl" />
      ) : null}
    </section>
  );
}

function roundOrNull(value: number | null): number | null {
  return value === null ? null : Math.round(value);
}

function SummaryItem({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0 rounded-xl border bg-card px-4 py-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("mt-1 truncate text-lg font-semibold tabular-nums", tone)}>{value}</dd>
    </div>
  );
}
