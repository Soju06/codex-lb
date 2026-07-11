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

export type CostPerDayChartProps = {
  startDate: string;
  endDate: string;
  data: DailyReportRow[];
};

export function CostPerDayChart({ startDate, endDate, data }: CostPerDayChartProps) {
  const chartData = buildContinuousDailyRows(startDate, endDate, data).map((d) => ({
    date: d.date.slice(5),
    cost: d.costUsd,
  }));

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="text-sm font-semibold text-foreground">Cost by Day</div>
      <div className="mt-4 h-[200px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 0 }}>
            <defs>
              <linearGradient id="costGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.3} />
                <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.05} />
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
              tickFormatter={(v: number) => `$${v}`}
            />
            <Tooltip
              content={<ChartTooltip names={{ cost: "Cost" }} formatValue={(v) => `$${v.toFixed(2)}`} />}
            />
            <Area
              type="monotone"
              dataKey="cost"
              stroke="#3b82f6"
              strokeWidth={2}
              fill="url(#costGrad)"
              dot={false}
              activeDot={{ r: 4, strokeWidth: 1.5, fill: "hsl(var(--popover))" }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
