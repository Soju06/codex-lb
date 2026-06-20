import { useState } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "@/components/lazy-recharts";
import type { ModelCostEntry } from "../schemas";
import { ChartTooltip } from "./chart-tooltip";
import { DistributionMetricToggle, type DistributionMetric } from "./distribution-metric-toggle";

export type ModelDistributionDonutProps = {
  data: ModelCostEntry[];
};

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#06b6d4"];

export function ModelDistributionDonut({ data }: ModelDistributionDonutProps) {
  const [metric, setMetric] = useState<DistributionMetric>("cost");
  const totalRequests = data.reduce((sum, entry) => sum + entry.requests, 0);
  const isCostMetric = metric === "cost";
  const chartData = data.map((entry) => ({
    ...entry,
    metricLabel: isCostMetric ? `$${entry.costUsd.toFixed(2)}` : String(entry.requests),
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
        <div className="text-sm font-semibold text-foreground">Distribution by Model</div>
        <DistributionMetricToggle metric={metric} onChange={setMetric} />
      </div>
      <div className="mt-4 flex items-center gap-4">
        <div className="relative h-[140px] w-[140px] shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={chartData}
                dataKey={isCostMetric ? "costUsd" : "requests"}
                nameKey="model"
                cx="50%"
                cy="50%"
                innerRadius={45}
                outerRadius={65}
                strokeWidth={0}
              >
                {chartData.map((entry, i) => (
                  <Cell key={entry.model} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                content={
                  <ChartTooltip
                    names={isCostMetric ? { costUsd: "Cost" } : { requests: "Requests" }}
                    formatValue={(value, dataKey) =>
                      dataKey === "requests" ? String(value) : `$${value.toFixed(2)}`
                    }
                  />
                }
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="flex-1 space-y-1.5 text-xs">
          {chartData.map((entry, i) => (
            <div
              key={entry.model}
              className="flex items-center justify-between rounded-md px-2 py-1 hover:bg-muted/50"
            >
              <div className="flex items-center gap-2">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                  style={{ background: COLORS[i % COLORS.length] }}
                />
                <span className="text-foreground">{entry.model}</span>
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
