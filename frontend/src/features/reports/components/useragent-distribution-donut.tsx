import { useState } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "@/components/lazy-recharts";
import type { UseragentCostEntry } from "../schemas";
import { ChartTooltip } from "./chart-tooltip";
import { DistributionMetricToggle, type DistributionMetric } from "./distribution-metric-toggle";
import { formatDistributionMetricValue } from "./distribution-metric-format";

export type UseragentDistributionDonutProps = {
  data: UseragentCostEntry[];
};

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#06b6d4"];
const UNKNOWN_COLOR = "#9ca3af";
const MISSING_USERAGENT_LABEL = "Missing User-Agent";

function getUseragentColor(useragent: string, index: number) {
  return useragent === MISSING_USERAGENT_LABEL ? UNKNOWN_COLOR : COLORS[index % COLORS.length];
}

export function UseragentDistributionDonut({ data }: UseragentDistributionDonutProps) {
  const [metric, setMetric] = useState<DistributionMetric>("cost");
  const totalCost = data.reduce((sum, entry) => sum + entry.costUsd, 0);
  const totalRequests = data.reduce((sum, entry) => sum + entry.requests, 0);
  const isCostMetric = metric === "cost";
  const totalMetricLabel = formatDistributionMetricValue(
    isCostMetric ? totalCost : totalRequests,
    metric,
  );
  const chartData = data.map((entry) => ({
    ...entry,
    metricLabel: formatDistributionMetricValue(
      isCostMetric ? entry.costUsd : entry.requests,
      metric,
    ),
    metricValue: isCostMetric ? entry.costUsd : entry.requests,
    metricPercentage: isCostMetric
      ? entry.percentage
      : totalRequests > 0
        ? (entry.requests / totalRequests) * 100
        : 0,
  }));
  const maxMetricLabelLength = chartData.reduce(
    (maxLength, entry) => Math.max(maxLength, entry.metricLabel.length),
    0,
  );

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm font-semibold text-foreground">Distribution by UserAgent</div>
        <DistributionMetricToggle metric={metric} onChange={setMetric} />
      </div>
      <div className="mt-4 flex items-center gap-4">
        <div className="relative h-[140px] w-[140px] shrink-0">
          <div className="pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center text-center">
            <span
              className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground"
              data-testid="useragent-distribution-center-label"
            >
              Total
            </span>
            <span
              className="max-w-[76px] text-sm font-semibold leading-tight tabular-nums text-foreground"
              data-testid="useragent-distribution-center-value"
            >
              {totalMetricLabel}
            </span>
          </div>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={chartData}
                dataKey={isCostMetric ? "costUsd" : "requests"}
                nameKey="useragent"
                cx="50%"
                cy="50%"
                innerRadius={45}
                outerRadius={65}
                strokeWidth={0}
              >
                {chartData.map((entry, i) => (
                  <Cell key={entry.useragent} fill={getUseragentColor(entry.useragent, i)} />
                ))}
              </Pie>
              <Tooltip
                content={
                  <ChartTooltip
                    names={isCostMetric ? { costUsd: "Cost" } : { requests: "Requests" }}
                    formatValue={(value) => formatDistributionMetricValue(value, metric)}
                  />
                }
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="flex-1 space-y-1.5 text-xs">
          {chartData.map((entry, i) => (
            <div
              key={entry.useragent}
              className="flex items-center justify-between rounded-md px-2 py-1 hover:bg-muted/50"
            >
              <div className="flex items-center gap-2">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                  style={{ background: getUseragentColor(entry.useragent, i) }}
                />
                <span className="text-foreground">{entry.useragent}</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="tabular-nums text-muted-foreground">{entry.metricPercentage.toFixed(1)}%</span>
                <span
                  className="inline-block text-right font-medium tabular-nums text-foreground"
                  style={{ minWidth: `${maxMetricLabelLength}ch` }}
                >
                  {entry.metricLabel}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
