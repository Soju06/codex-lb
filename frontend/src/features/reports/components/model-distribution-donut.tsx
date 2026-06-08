import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import type { ModelCostEntry } from "../schemas";

export type ModelDistributionDonutProps = {
  data: ModelCostEntry[];
};

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#06b6d4"];

export function ModelDistributionDonut({ data }: ModelDistributionDonutProps) {
  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="text-sm font-semibold text-foreground">Distribuição por Modelo</div>
      <div className="mt-4 flex items-center gap-4">
        <div className="h-[140px] w-[140px] shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="costUsd"
                nameKey="model"
                cx="50%"
                cy="50%"
                innerRadius={45}
                outerRadius={65}
                strokeWidth={0}
              >
                {data.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  borderRadius: "8px",
                  border: "1px solid hsl(var(--border))",
                  background: "hsl(var(--popover))",
                }}
                formatter={(value) => {
                  if (typeof value !== "number") return [String(value), "Custo"];
                  return [`$${value.toFixed(2)}`, "Custo"];
                }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="flex-1 space-y-1.5 text-xs">
          {data.map((entry, i) => (
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
                <span className="text-muted-foreground">{entry.percentage}%</span>
                <span className="font-medium text-foreground">${entry.costUsd.toFixed(2)}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
