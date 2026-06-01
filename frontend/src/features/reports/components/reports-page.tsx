import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listAccounts } from "@/features/accounts/api";
import { useReports } from "@/features/reports/hooks/use-reports";
import { ReportsFilters, type ReportsFiltersState } from "./reports-filters";
import { ReportsSummaryCards } from "./reports-summary-cards";
import { CostPerDayChart } from "./cost-per-day-chart";
import { TokensPerDayChart } from "./tokens-per-day-chart";
import { ModelDistributionDonut } from "./model-distribution-donut";
import { DailyDetailTable } from "./daily-detail-table";
import { daysAgoLocalISO, localDateISO } from "../date";

const DEFAULT_FILTERS: ReportsFiltersState = {
  startDate: daysAgoLocalISO(6),
  endDate: localDateISO(),
  accountId: [],
  model: "",
};

export type ReportsPageProps = {
  initialFilters?: Partial<ReportsFiltersState>;
};

export function ReportsPage({ initialFilters }: ReportsPageProps = {}) {
  const [filters, setFilters] = useState<ReportsFiltersState>({
    ...DEFAULT_FILTERS,
    ...initialFilters,
  });
  const { data, isLoading } = useReports(filters);
  const modelCatalogFilters = useMemo(
    () => ({ ...filters, model: "" }),
    [filters],
  );
  const { data: modelCatalogData } = useReports(modelCatalogFilters);
  const { data: accountsData } = useQuery({
    queryKey: ["accounts", "reports-filter"],
    queryFn: listAccounts,
  });

  const accountOptions = useMemo(
    () =>
      (accountsData?.accounts ?? []).map((account) => ({
        value: account.accountId,
        label: account.alias || account.displayName || account.email || account.accountId,
        isEmail: !account.alias,
      })),
    [accountsData],
  );

  const modelOptions = useMemo(
    () =>
      (modelCatalogData?.byModel ?? []).map((entry) => ({
        value: entry.model,
        label: entry.model,
      })),
    [modelCatalogData],
  );

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

      {isLoading ? (
        <div className="flex items-center justify-center py-20 text-sm text-muted-foreground">
          Carregando...
        </div>
      ) : data ? (
        <>
          <ReportsSummaryCards summary={data.summary} />
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <CostPerDayChart data={data.daily} />
            <TokensPerDayChart data={data.daily} />
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="lg:col-span-1">
              <ModelDistributionDonut data={data.byModel} />
            </div>
            <div className="lg:col-span-2">
              <DailyDetailTable data={data.daily} />
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
