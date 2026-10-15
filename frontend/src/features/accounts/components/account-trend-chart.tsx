import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "@/components/lazy-recharts";

import { useChartColors } from "@/hooks/use-chart-colors";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import type { UsageTrendPoint } from "@/features/accounts/schemas";
import { formatChartDateTime } from "@/utils/formatters";

type MergedPoint = {
  t: string;
  primary: number | null;
  secondary: number | null;
  secondaryScheduled?: number;
};

/** Align observations and scheduled values on distinct instants across all series. */
function mergePoints(
  primary: UsageTrendPoint[],
  secondary: UsageTrendPoint[],
  secondaryScheduled: UsageTrendPoint[],
): MergedPoint[] {
  const secondaryScheduledMap = new Map(secondaryScheduled.map((p) => [Date.parse(p.t), p.v]));
  const timestamps = [...new Set([...primary, ...secondary, ...secondaryScheduled].map((p) => Date.parse(p.t)))]
    .sort((a, b) => a - b);
  const primaryValues = interpolatePoints(primary, timestamps);
  const secondaryValues = interpolatePoints(secondary, timestamps);

  return timestamps.map((t, index) => ({
    t: new Date(t).toISOString(),
    primary: primaryValues[index],
    secondary: secondaryValues[index],
    secondaryScheduled: secondaryScheduledMap.get(t),
  }));
}

/** Fill gaps between observations while leaving time before the first sample unknown. */
function interpolatePoints(points: UsageTrendPoint[], timestamps: number[]): (number | null)[] {
  const sorted = [...points].sort((a, b) => Date.parse(a.t) - Date.parse(b.t));
  let nextIndex = 0;
  return timestamps.map((time) => {
    while (nextIndex < sorted.length && Date.parse(sorted[nextIndex].t) <= time) {
      nextIndex += 1;
    }
    const previous = sorted[nextIndex - 1];
    const next = sorted[nextIndex];
    if (!previous) return null;
    if (!next) return previous.v;
    const fraction = (time - Date.parse(previous.t)) / (Date.parse(next.t) - Date.parse(previous.t));
    return previous.v + fraction * (next.v - previous.v);
  });
}

function formatXTick(isoStr: string): string {
  return isoStr.slice(5, 10);
}

const SERIES_META: Record<string, { label: string }> = {
  primary: { label: "Primary" },
  secondary: { label: "Secondary" },
  secondaryScheduled: { label: "Weekly plan" },
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
  monthly?: boolean;
};

/** Render quota series labels according to the account's quota window. */
function CustomTooltip({ active, payload, label, monthly }: ChartTooltipProps) {
  const { t } = useTranslation();
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
            <span className="text-muted-foreground">{meta ? (monthly && entry.dataKey === "secondary" ? t("common.quota.monthly") : monthly && entry.dataKey === "secondaryScheduled" ? t("accounts.usage.monthlyPlan") : t(`accounts.trend.series.${entry.dataKey}`, { defaultValue: meta.label })) : ""}</span>
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
  secondaryScheduled?: UsageTrendPoint[];
  monthly?: boolean;
};

const EMPTY_TREND_POINTS: UsageTrendPoint[] = [];

/** Plot merged account quota observations and any scheduled quota values. */
export function AccountTrendChart({
  primary,
  secondary,
  secondaryScheduled = EMPTY_TREND_POINTS,
  monthly = false,
}: AccountTrendChartProps) {
  const { t } = useTranslation();
  const chartColors = useChartColors();
  const reducedMotion = useReducedMotion();
  const c1 = chartColors[0];
  const c2 = chartColors[1];
  const data = useMemo(
    () => mergePoints(primary, secondary, secondaryScheduled),
    [primary, secondary, secondaryScheduled],
  );

  if (data.length === 0) {
    return (
      <div className="flex h-[200px] items-center justify-center text-xs text-muted-foreground">
        {t("accounts.trend.empty")}
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
          tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          minTickGap={50}
          dy={4}
        />
        <YAxis
          domain={[0, 100]}
          ticks={[0, 25, 50, 75, 100]}
          tickFormatter={(v: number) => `${v}%`}
          tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          width={38}
        />
        <Tooltip
          content={<CustomTooltip monthly={monthly} />}
          cursor={{ stroke: "hsl(var(--border))", strokeWidth: 1 }}
        />
        {primary.length > 0 && (
          <Area
            type="monotone"
            dataKey="primary"
            stroke={c1}
            strokeWidth={1.5}
            fill="url(#trend-primary)"
            dot={false}
            activeDot={{ r: 3, strokeWidth: 1.5, fill: "hsl(var(--popover))" }}
            isAnimationActive={!reducedMotion}
            animationDuration={500}
          />
        )}
        {secondary.length > 0 && (
          <Area
            type="monotone"
            dataKey="secondary"
            stroke={c2}
            strokeWidth={1.5}
            fill="url(#trend-secondary)"
            dot={false}
            activeDot={{ r: 3, strokeWidth: 1.5, fill: "hsl(var(--popover))" }}
            isAnimationActive={!reducedMotion}
            animationDuration={500}
            animationBegin={100}
          />
        )}
        {secondaryScheduled.length > 0 && (
          <Line
            type="linear"
            dataKey="secondaryScheduled"
            stroke={c2}
            strokeWidth={1.25}
            strokeDasharray="5 5"
            dot={false}
            activeDot={{ r: 3, strokeWidth: 1.5, fill: "hsl(var(--popover))" }}
            connectNulls={false}
            isAnimationActive={!reducedMotion}
            animationDuration={500}
            animationBegin={150}
          />
        )}
      </AreaChart>
    </ResponsiveContainer>
  );
}
