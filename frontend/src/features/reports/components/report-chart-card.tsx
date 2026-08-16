import { BarChart3 } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/empty-state";

export type ReportChartCardProps = {
  title: string;
  empty: boolean;
  children: ReactNode;
};

export function ReportChartCard({ title, empty, children }: ReportChartCardProps) {
  const { t } = useTranslation();

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="text-sm font-semibold text-foreground">{title}</div>
      {empty ? (
        <div className="mt-4">
          <EmptyState
            icon={BarChart3}
            title={t("reports.charts.emptyTitle")}
            description={t("reports.charts.emptyDescription")}
          />
        </div>
      ) : (
        <div className="mt-4 h-[200px]">{children}</div>
      )}
    </div>
  );
}
