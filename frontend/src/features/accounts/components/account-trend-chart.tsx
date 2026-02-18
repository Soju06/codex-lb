import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useChartColors } from "@/hooks/use-chart-colors";
import type { UsageTrendPoint } from "@/features/accounts/schemas";

type MergedPoint = {
  t: string;
  primary: number;
  secondary: number;
};

function mergePoints(
  primary: UsageTrendPoint[],
  secondary: UsageTrendPoint[],
): MergedPoint[] {
  const secondaryMap = new Map(secondary.map((p) => [p.t, p.v]));
  return primary.map((p) => ({
    t: p.t,
    primary: p.v,
    secondary: secondaryMap.get(p.t) ?? 0,
  }));
}

function formatXTick(isoStr: string): string {
  const d = new Date(isoStr);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

const SERIES_META: Record<string, { label: string }> = {
  primary: { label: "Primary" },
  secondary: { label: "Secondary" },
};

type ChartTooltipPayloadEntry = {
  dataKey?: string | number;
  value?: number;
  color?: string;
};

type ChartTooltipProps = {
  active?: boolean;
  payload?: ChartTooltipPayloadEntry[];
  label?: string;
};

function CustomTooltip({ active, payload, label }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = new Date(label as string);
  const heading = d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-popover-foreground shadow-md">
      <p className="mb-1 text-[11px] text-muted-foreground">{heading}</p>
      {payload.map((entry: ChartTooltipPayloadEntry) => {
        const meta = SERIES_META[entry.dataKey as string];
        return (
          <div key={entry.dataKey} className="flex items-center gap-2 text-xs">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: entry.color }}
            />
            <span className="text-muted-foreground">{meta?.label}</span>
            <span className="ml-auto tabular-nums font-medium">{entry.value?.toFixed(1)}%</span>
          </div>
        );
      })}
    </div>
  );
}

const CHART_MARGIN = { top: 4, right: 8, bottom: 0, left: 0 } as const;

export type AccountTrendChartProps = {
  primary: UsageTrendPoint[];
  secondary: UsageTrendPoint[];
};

export function AccountTrendChart({ primary, secondary }: AccountTrendChartProps) {
  const chartColors = useChartColors();
  const c1 = chartColors[0];
  const c2 = chartColors[1];
  const data = useMemo(() => mergePoints(primary, secondary), [primary, secondary]);

  if (data.length === 0) {
    return (
      <div className="flex h-[200px] items-center justify-center text-xs text-muted-foreground">
        No trend data available
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data} margin={CHART_MARGIN}>
        <defs>
          <linearGradient id="trend-primary" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={c1} stopOpacity={0.12} />
            <stop offset="100%" stopColor={c1} stopOpacity={0} />
          </linearGradient>
          <linearGradient id="trend-secondary" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={c2} stopOpacity={0.12} />
            <stop offset="100%" stopColor={c2} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="currentColor" opacity={0.06} />
        <XAxis
          dataKey="t"
          tickFormatter={formatXTick}
          tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
          tickLine={false}
          axisLine={false}
          minTickGap={50}
          dy={4}
        />
        <YAxis
          domain={[0, 100]}
          ticks={[0, 25, 50, 75, 100]}
          tickFormatter={(v: number) => `${v}%`}
          tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
          tickLine={false}
          axisLine={false}
          width={38}
        />
        <Tooltip
          content={<CustomTooltip />}
          cursor={{ stroke: "hsl(var(--border))", strokeWidth: 1 }}
        />
        <Area
          type="monotone"
          dataKey="primary"
          stroke={c1}
          strokeWidth={1.5}
          fill="url(#trend-primary)"
          dot={false}
          activeDot={{ r: 3, strokeWidth: 1.5, fill: "hsl(var(--popover))" }}
          isAnimationActive
          animationDuration={500}
        />
        <Area
          type="monotone"
          dataKey="secondary"
          stroke={c2}
          strokeWidth={1.5}
          fill="url(#trend-secondary)"
          dot={false}
          activeDot={{ r: 3, strokeWidth: 1.5, fill: "hsl(var(--popover))" }}
          isAnimationActive
          animationDuration={500}
          animationBegin={100}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
