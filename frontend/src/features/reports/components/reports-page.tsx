import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { listAccounts } from "@/features/accounts/api";
import { getRequestLogOptions } from "@/features/dashboard/api";
import { useReports } from "@/features/reports/hooks/use-reports";
import { useReportChartVisibility } from "@/features/reports/hooks/use-report-chart-visibility";
import { getErrorMessageOrNull } from "@/utils/errors";
import { ReportsFilters, type ReportsFiltersState } from "./reports-filters";
import { ReportsSummaryCards } from "./reports-summary-cards";
import type { CostPerDayChartProps } from "./cost-per-day-chart";
import type { TokensPerDayChartProps } from "./tokens-per-day-chart";
import type { TimeToFirstTokenChartProps } from "./time-to-first-token-chart";
import type { TokensPerSecondChartProps } from "./tokens-per-second-chart";
import type { QueueWaitChartProps } from "./queue-wait-chart";
import type { ModelDistributionDonutProps } from "./model-distribution-donut";
import type { UseragentDistributionDonutProps } from "./useragent-distribution-donut";
import { DailyDetailTable } from "./daily-detail-table";
import {
  daysAgoLocalISO,
  getBrowserReportsTimeZone,
  isReportDateRangeValid,
  localDateISO,
} from "../date";

const CostPerDayChart = lazy(() =>
  import("./cost-per-day-chart").then((module) => ({
    default: (props: CostPerDayChartProps) => <module.CostPerDayChart {...props} />,
  })),
);
const TokensPerDayChart = lazy(() =>
  import("./tokens-per-day-chart").then((module) => ({
    default: (props: TokensPerDayChartProps) => <module.TokensPerDayChart {...props} />,
  })),
);
const TimeToFirstTokenChart = lazy(() =>
  import("./time-to-first-token-chart").then((module) => ({
    default: (props: TimeToFirstTokenChartProps) => <module.TimeToFirstTokenChart {...props} />,
  })),
);
const TokensPerSecondChart = lazy(() =>
  import("./tokens-per-second-chart").then((module) => ({
    default: (props: TokensPerSecondChartProps) => <module.TokensPerSecondChart {...props} />,
  })),
);
const QueueWaitChart = lazy(() =>
  import("./queue-wait-chart").then((module) => ({
    default: (props: QueueWaitChartProps) => <module.QueueWaitChart {...props} />,
  })),
);
const ModelDistributionDonut = lazy(() =>
  import("./model-distribution-donut").then((module) => ({
    default: (props: ModelDistributionDonutProps) => <module.ModelDistributionDonut {...props} />,
  })),
);
const UseragentDistributionDonut = lazy(() =>
  import("./useragent-distribution-donut").then((module) => ({
    default: (props: UseragentDistributionDonutProps) => (
      <module.UseragentDistributionDonut {...props} />
    ),
  })),
);

const REPORTS_TIMEZONE_REFRESH_INTERVAL_MS = 60_000;
const DEFAULT_PRESET_DAYS = 7;

const createDefaultFilters = (): ReportsFiltersState => ({
  startDate: daysAgoLocalISO(6),
  endDate: localDateISO(),
  accountId: [],
  apiKeyId: [],
  model: "",
  useragent: "",
});

export type ReportsPageProps = {
  initialFilters?: Partial<ReportsFiltersState>;
};

export function ReportsPage({ initialFilters }: ReportsPageProps = {}) {
  const { t } = useTranslation();
  const [filters, setFilters] = useState<ReportsFiltersState>(() => ({
    ...createDefaultFilters(),
    ...initialFilters,
  }));
  const [selectedPresetDays, setSelectedPresetDays] = useState<number | null>(
    DEFAULT_PRESET_DAYS,
  );
  const [reportsTimeZone, setReportsTimeZone] = useState<string | undefined>(() =>
    getBrowserReportsTimeZone(),
  );
  const { visibleChartIds, setVisibleChartIds } = useReportChartVisibility();

  useEffect(() => {
    const refreshReportsTimeZone = () => {
      setReportsTimeZone((currentTimeZone) => {
        const nextTimeZone = getBrowserReportsTimeZone();
        return currentTimeZone === nextTimeZone ? currentTimeZone : nextTimeZone;
      });
    };

    const intervalId = window.setInterval(
      refreshReportsTimeZone,
      REPORTS_TIMEZONE_REFRESH_INTERVAL_MS,
    );

    window.addEventListener("focus", refreshReportsTimeZone);
    document.addEventListener("visibilitychange", refreshReportsTimeZone);

    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("focus", refreshReportsTimeZone);
      document.removeEventListener("visibilitychange", refreshReportsTimeZone);
    };
  }, []);

  const reportsQuery = useReports(filters, reportsTimeZone);
  const filterCatalogFilters = useMemo(
    () => ({ ...filters, model: "", useragent: "" }),
    [filters],
  );
  const filterCatalogQuery = useReports(filterCatalogFilters, reportsTimeZone);
  const {
    data: accountsData,
    error: accountsError,
    refetch: refetchAccounts,
  } = useQuery({
    queryKey: ["accounts", "reports-filter"],
    queryFn: listAccounts,
  });
  const {
    data: apiKeysOptionsData,
    error: apiKeysError,
    refetch: refetchApiKeys,
  } = useQuery({
    queryKey: ["request-log-options", "reports-filter"],
    queryFn: () => getRequestLogOptions(),
  });

  const accountOptions = useMemo(
    () =>
      (accountsData?.accounts ?? []).map((account) => ({
        value: account.accountId,
        label:
          account.alias ||
          account.displayName ||
          account.email ||
          account.accountId,
        isEmail: !account.alias,
      })),
    [accountsData],
  );

  const apiKeyOptions = useMemo(
    () =>
      (apiKeysOptionsData?.apiKeys ?? []).map((key) => ({
        value: key.id,
        label: key.keyPrefix ? `${key.name} · ${key.keyPrefix}` : key.name,
      })),
    [apiKeysOptionsData],
  );

  const modelOptions = useMemo(
    () =>
      (filterCatalogQuery.data?.byModel ?? []).map((entry) => ({
        value: entry.model,
        label: entry.model,
      })),
    [filterCatalogQuery.data],
  );

  const useragentOptions = useMemo(
    () =>
      (filterCatalogQuery.data?.byUseragent ?? []).map((entry) => ({
        value: entry.useragent,
        label: entry.useragent,
      })),
    [filterCatalogQuery.data],
  );

  const mainReportsError = getErrorMessageOrNull(reportsQuery.error);
  const sharedOptionsError = getErrorMessageOrNull(filterCatalogQuery.error);
  const accountOptionsError = getErrorMessageOrNull(accountsError);
  const apiKeyOptionsError = getErrorMessageOrNull(apiKeysError);

  const hasAnyError = Boolean(
    mainReportsError || sharedOptionsError || accountOptionsError || apiKeyOptionsError,
  );

  const handleRetry = async () => {
    if (!isReportDateRangeValid(filters.startDate, filters.endDate)) {
      await Promise.allSettled([refetchAccounts(), refetchApiKeys()]);
      return;
    }

    await Promise.allSettled([
      reportsQuery.refetch(),
      filterCatalogQuery.refetch(),
      refetchAccounts(),
      refetchApiKeys(),
    ]);
  };

  const handlePresetSelect = (days: number) => {
    setSelectedPresetDays(days);
    setFilters((current) => ({
      ...current,
      startDate: daysAgoLocalISO(days - 1),
      endDate: localDateISO(),
    }));
  };

  const handleFiltersChange = (nextFilters: ReportsFiltersState) => {
    if (
      nextFilters.startDate !== filters.startDate ||
      nextFilters.endDate !== filters.endDate
    ) {
      setSelectedPresetDays(null);
    }
    setFilters(nextFilters);
  };

  return (
    <div className="mx-auto w-full max-w-[1500px] flex-1 space-y-6 px-4 py-8 sm:px-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          {t("reports.page.title")}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("reports.page.subtitle")}
        </p>
      </div>

      <ReportsFilters
        filters={filters}
        selectedPresetDays={selectedPresetDays}
        accountOptions={accountOptions}
        apiKeyOptions={apiKeyOptions}
        modelOptions={modelOptions}
        useragentOptions={useragentOptions}
        visibleChartIds={visibleChartIds}
        onPresetSelect={handlePresetSelect}
        onFiltersChange={handleFiltersChange}
        onVisibleChartIdsChange={setVisibleChartIds}
      />

      {mainReportsError ? (
        <AlertMessage variant="error">
          {t("reports.errors.data", { error: mainReportsError })}
        </AlertMessage>
      ) : null}
      {sharedOptionsError ? (
        <div className="flex items-center justify-between gap-2">
          <AlertMessage variant="error" className="flex-1">
            {t("reports.errors.options", { error: sharedOptionsError })}
          </AlertMessage>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              void handleRetry();
            }}
          >
            {t("common.actions.retry")}
          </Button>
        </div>
      ) : null}
      {accountOptionsError ? (
        <div className="flex items-center justify-between gap-2">
          <AlertMessage variant="error" className="flex-1">
            {t("reports.errors.accounts", { error: accountOptionsError })}
          </AlertMessage>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              void handleRetry();
            }}
          >
            {t("common.actions.retry")}
          </Button>
        </div>
      ) : null}
      {apiKeyOptionsError ? (
        <div className="flex items-center justify-between gap-2">
          <AlertMessage variant="error" className="flex-1">
            {t("reports.errors.apiKeys", { error: apiKeyOptionsError })}
          </AlertMessage>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              void handleRetry();
            }}
          >
            {t("common.actions.retry")}
          </Button>
        </div>
      ) : null}

      {reportsQuery.isLoading ? (
        <div className="flex items-center justify-center py-20 text-sm text-muted-foreground">
          {t("common.loading")}
        </div>
      ) : reportsQuery.data ? (
        <>
          <ReportsSummaryCards
            summary={reportsQuery.data.summary}
            comparison={reportsQuery.data.comparison}
          />
          {visibleChartIds.length > 0 ? (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {visibleChartIds.includes("costByDay") ? (
                <Suspense fallback={<div className="h-[270px] rounded-xl border bg-card" />}>
                  <CostPerDayChart
                    startDate={filters.startDate}
                    endDate={filters.endDate}
                    data={reportsQuery.data.daily}
                  />
                </Suspense>
              ) : null}
              {visibleChartIds.includes("tokensByDay") ? (
                <Suspense fallback={<div className="h-[270px] rounded-xl border bg-card" />}>
                  <TokensPerDayChart
                    startDate={filters.startDate}
                    endDate={filters.endDate}
                    data={reportsQuery.data.daily}
                  />
                </Suspense>
              ) : null}
              {visibleChartIds.includes("timeToFirstToken") ? (
                <Suspense fallback={<div className="h-[270px] rounded-xl border bg-card" />}>
                  <TimeToFirstTokenChart
                    startDate={filters.startDate}
                    endDate={filters.endDate}
                    data={reportsQuery.data.daily}
                  />
                </Suspense>
              ) : null}
              {visibleChartIds.includes("tokensPerSecond") ? (
                <Suspense fallback={<div className="h-[270px] rounded-xl border bg-card" />}>
                  <TokensPerSecondChart
                    startDate={filters.startDate}
                    endDate={filters.endDate}
                    data={reportsQuery.data.daily}
                  />
                </Suspense>
              ) : null}
              {visibleChartIds.includes("queueWait") ? (
                <Suspense fallback={<div className="h-[270px] rounded-xl border bg-card" />}>
                  <QueueWaitChart
                    startDate={filters.startDate}
                    endDate={filters.endDate}
                    data={reportsQuery.data.daily}
                  />
                </Suspense>
              ) : null}
            </div>
          ) : null}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="space-y-4 lg:col-span-1">
              <Suspense fallback={<div className="h-[220px] rounded-xl border bg-card" />}>
                <ModelDistributionDonut data={reportsQuery.data.byModel} />
              </Suspense>
              <Suspense fallback={<div className="h-[220px] rounded-xl border bg-card" />}>
                <UseragentDistributionDonut data={reportsQuery.data.byUseragent} />
              </Suspense>
            </div>
            <div className="lg:col-span-2">
              <DailyDetailTable
                startDate={filters.startDate}
                endDate={filters.endDate}
                data={reportsQuery.data.daily}
              />
            </div>
          </div>
        </>
      ) : hasAnyError ? (
        <div className="space-y-3 rounded-xl border bg-card p-4">
          <AlertMessage variant="warning">
            {t("reports.errors.partial")}
          </AlertMessage>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              void handleRetry();
            }}
          >
            {t("common.actions.retry")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
