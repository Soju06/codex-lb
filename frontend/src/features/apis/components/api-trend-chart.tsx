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
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import type { ApiKeyTrendPoint } from "@/features/apis/schemas";
import { formatChartDateTime, formatCompactNumber, formatCurrency } from "@/utils/formatters";

type MergedPoint = {
  t: string;
  cost: number;
  tokens: number;
};

function mergePoints(
  cost: ApiKeyTrendPoint[],
  tokens: ApiKeyTrendPoint[],
): MergedPoint[] {
  const costMap = new Map(cost.map((p) => [p.t, p.v]));
  const tokensMap = new Map(tokens.map((p) => [p.t, p.v]));

  const allTimes = new Set([...costMap.keys(), ...tokensMap.keys()]);
  if (allTimes.size === 0) return [];

  return Array.from(allTimes)
    .sort()
    .map((t) => ({
      t,
      cost: costMap.get(t) ?? 0,
      tokens: tokensMap.get(t) ?? 0,
    }));
}

function formatXTick(isoStr: string): string {
  const d = new Date(isoStr);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatCostTick(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return "<$0.01";
  return `$${value.toFixed(2)}`;
}

function formatTokenTick(value: number): string {
  if (value === 0) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(0)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)}K`;
  return String(value);
}

const SERIES_META: Record<string, { label: string; formatter: (v: number) => string }> = {
  cost: { label: "Cost", formatter: formatCurrency },
  tokens: { label: "Tokens", formatter: (v) => formatCompactNumber(v) },
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
  const heading = formatChartDateTime(label as string);
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
            <span className="ml-auto tabular-nums font-medium">
              {meta?.formatter(entry.value ?? 0)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

const CHART_MARGIN = { top: 4, right: 48, bottom: 0, left: 0 } as const;

export type ApiTrendChartProps = {
  cost: ApiKeyTrendPoint[];
  tokens: ApiKeyTrendPoint[];
};

export function ApiTrendChart({ cost, tokens }: ApiTrendChartProps) {
  const chartColors = useChartColors();
  const reducedMotion = useReducedMotion();
  const c1 = chartColors[0];
  const c2 = chartColors[1];
  const data = useMemo(() => mergePoints(cost, tokens), [cost, tokens]);

  const maxTokens = useMemo(() => Math.max(...data.map((d) => d.tokens), 1), [data]);
  const maxCost = useMemo(() => Math.max(...data.map((d) => d.cost), 0.01), [data]);

  if (data.length === 0) {
    return (
      <div className="flex h-[280px] items-center justify-center text-xs text-muted-foreground">
        No trend data available
      </div>
    );
  }

  const tokenTicks = [0, maxTokens * 0.5, maxTokens];
  const costTicks = [0, maxCost * 0.5, maxCost];

  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart data={data} margin={CHART_MARGIN}>
        <defs>
          <linearGradient id="api-trend-cost" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={c1} stopOpacity={0.15} />
            <stop offset="100%" stopColor={c1} stopOpacity={0} />
          </linearGradient>
          <linearGradient id="api-trend-tokens" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={c2} stopOpacity={0.15} />
            <stop offset="100%" stopColor={c2} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="currentColor" opacity={0.06} />
        <XAxis
          dataKey="t"
          tickFormatter={formatXTick}
          tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          minTickGap={50}
          dy={4}
        />
        <YAxis
          yAxisId="tokens"
          orientation="left"
          domain={[0, maxTokens * 1.1]}
          ticks={tokenTicks}
          tickFormatter={formatTokenTick}
          tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          width={48}
        />
        <YAxis
          yAxisId="cost"
          orientation="right"
          domain={[0, maxCost * 1.1]}
          ticks={costTicks}
          tickFormatter={formatCostTick}
          tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          width={48}
        />
        <Tooltip
          content={<CustomTooltip />}
          cursor={{ stroke: "hsl(var(--border))", strokeWidth: 1 }}
        />
        <Area
          yAxisId="tokens"
          type="monotone"
          dataKey="tokens"
          stroke={c2}
          strokeWidth={1.5}
          fill="url(#api-trend-tokens)"
          dot={false}
          activeDot={{ r: 3, strokeWidth: 1.5, fill: "hsl(var(--popover))" }}
          isAnimationActive={!reducedMotion}
          animationDuration={500}
        />
        <Area
          yAxisId="cost"
          type="monotone"
          dataKey="cost"
          stroke={c1}
          strokeWidth={1.5}
          fill="url(#api-trend-cost)"
          dot={false}
          activeDot={{ r: 3, strokeWidth: 1.5, fill: "hsl(var(--popover))" }}
          isAnimationActive={!reducedMotion}
          animationDuration={500}
          animationBegin={100}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
