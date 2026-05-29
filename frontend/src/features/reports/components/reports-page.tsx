import { useState } from "react";
import { useReports } from "@/features/reports/hooks/use-reports";
import { ReportsFilters, type ReportsFiltersState } from "./reports-filters";
import { ReportsSummaryCards } from "./reports-summary-cards";
import { CostPerDayChart } from "./cost-per-day-chart";
import { TokensPerDayChart } from "./tokens-per-day-chart";
import { ModelDistributionDonut } from "./model-distribution-donut";
import { DailyDetailTable } from "./daily-detail-table";

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgoISO(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

const DEFAULT_FILTERS: ReportsFiltersState = {
  startDate: daysAgoISO(7),
  endDate: todayISO(),
  accountId: [],
  model: "",
};

export function ReportsPage() {
  const [filters, setFilters] = useState<ReportsFiltersState>(DEFAULT_FILTERS);
  const { data, isLoading } = useReports(filters);

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

      <ReportsFilters filters={filters} onFiltersChange={setFilters} />

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
