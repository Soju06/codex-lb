import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { listAccounts } from "@/features/accounts/api";
import { useReports } from "@/features/reports/hooks/use-reports";
import { getErrorMessageOrNull } from "@/utils/errors";
import { ReportsFilters, type ReportsFiltersState } from "./reports-filters";
import { ReportsSummaryCards } from "./reports-summary-cards";
import { CostPerDayChart } from "./cost-per-day-chart";
import { TokensPerDayChart } from "./tokens-per-day-chart";
import { ModelDistributionDonut } from "./model-distribution-donut";
import { DailyDetailTable } from "./daily-detail-table";
import { daysAgoLocalISO, localDateISO } from "../date";

const createDefaultFilters = (): ReportsFiltersState => ({
  startDate: daysAgoLocalISO(6),
  endDate: localDateISO(),
  accountId: [],
  model: "",
});

export type ReportsPageProps = {
  initialFilters?: Partial<ReportsFiltersState>;
};

export function ReportsPage({ initialFilters }: ReportsPageProps = {}) {
  const [filters, setFilters] = useState<ReportsFiltersState>(() => ({
    ...createDefaultFilters(),
    ...initialFilters,
  }));
  const reportsQuery = useReports(filters);
  const modelCatalogFilters = useMemo(
    () => ({ ...filters, model: "" }),
    [filters],
  );
  const modelCatalogQuery = useReports(modelCatalogFilters);
  const accountsQuery = useQuery({
    queryKey: ["accounts", "reports-filter"],
    queryFn: listAccounts,
  });

  const accountOptions = useMemo(
    () =>
      (accountsQuery.data?.accounts ?? []).map((account) => ({
        value: account.accountId,
        label:
          account.alias ||
          account.displayName ||
          account.email ||
          account.accountId,
        isEmail: !account.alias,
      })),
    [accountsQuery.data],
  );

  const modelOptions = useMemo(
    () =>
      (modelCatalogQuery.data?.byModel ?? []).map((entry) => ({
        value: entry.model,
        label: entry.model,
      })),
    [modelCatalogQuery.data],
  );

  const mainReportsError = getErrorMessageOrNull(reportsQuery.error);
  const modelOptionsError = getErrorMessageOrNull(modelCatalogQuery.error);
  const accountOptionsError = getErrorMessageOrNull(accountsQuery.error);

  const hasAnyError = Boolean(
    mainReportsError || modelOptionsError || accountOptionsError,
  );

  const handleRetry = async () => {
    await Promise.allSettled([
      reportsQuery.refetch(),
      modelCatalogQuery.refetch(),
      accountsQuery.refetch(),
    ]);
  };

  return (
    <div className="mx-auto w-full max-w-[1500px] flex-1 space-y-6 px-4 py-8 sm:px-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Relatório de Custo
        </h1>
        <p className="text-sm text-muted-foreground">
          Histórico de utilização por período
        </p>
      </div>

      <ReportsFilters
        filters={filters}
        accountOptions={accountOptions}
        modelOptions={modelOptions}
        onFiltersChange={setFilters}
      />

      {mainReportsError ? (
        <AlertMessage variant="error">
          Failed to load report data: {mainReportsError}
        </AlertMessage>
      ) : null}
      {modelOptionsError ? (
        <AlertMessage variant="error">
          Failed to load model options: {modelOptionsError}
        </AlertMessage>
      ) : null}
      {accountOptionsError ? (
        <AlertMessage variant="error">
          Failed to load account options: {accountOptionsError}
        </AlertMessage>
      ) : null}

      {reportsQuery.isLoading ? (
        <div className="flex items-center justify-center py-20 text-sm text-muted-foreground">
          Carregando...
        </div>
      ) : reportsQuery.data ? (
        <>
          <ReportsSummaryCards summary={reportsQuery.data.summary} />
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <CostPerDayChart data={reportsQuery.data.daily} />
            <TokensPerDayChart data={reportsQuery.data.daily} />
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="lg:col-span-1">
              <ModelDistributionDonut data={reportsQuery.data.byModel} />
            </div>
            <div className="lg:col-span-2">
              <DailyDetailTable data={reportsQuery.data.daily} />
            </div>
          </div>
        </>
      ) : hasAnyError ? (
        <div className="space-y-3 rounded-xl border bg-card p-4">
          <AlertMessage variant="warning">
            Some report data could not be loaded. Try reloading.
          </AlertMessage>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              void handleRetry();
            }}
          >
            Retry
          </Button>
        </div>
      ) : null}
    </div>
  );
}
