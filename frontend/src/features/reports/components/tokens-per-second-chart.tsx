import { useTranslation } from "react-i18next";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "@/components/lazy-recharts";
import type { DailyReportRow } from "../schemas";
import { buildContinuousDailyRows } from "../daily-series";
import { ChartTooltip } from "./chart-tooltip";

export type TokensPerSecondChartProps = {
  startDate: string;
  endDate: string;
  data: DailyReportRow[];
};

function formatTps(value: number): string {
  return value.toFixed(1);
}

export function TokensPerSecondChart({ startDate, endDate, data }: TokensPerSecondChartProps) {
  const { t } = useTranslation();
  const chartData = buildContinuousDailyRows(startDate, endDate, data).map((d) => ({
    date: d.date.slice(5),
    tps: d.medianTps ?? 0,
  }));

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="text-sm font-semibold text-foreground">{t("reports.charts.tokensPerSecond")}</div>
      <div className="mt-4 h-[200px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 0 }}>
            <defs>
              <linearGradient id="tpsGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#10b981" stopOpacity={0.3} />
                <stop offset="100%" stopColor="#10b981" stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
              tickFormatter={formatTps}
            />
            <Tooltip
              content={<ChartTooltip names={{ tps: t("reports.charts.medianTps") }} formatValue={formatTps} />}
            />
            <Area
              type="monotone"
              dataKey="tps"
              stroke="#10b981"
              strokeWidth={2}
              fill="url(#tpsGrad)"
              dot={false}
              activeDot={{ r: 4, strokeWidth: 1.5, fill: "hsl(var(--popover))" }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
