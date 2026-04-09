import { useEffect, useRef, useState } from "react";
import { Cell, Pie, PieChart, Sector, type PieSectorShapeProps } from "recharts";

import { buildDonutPalette } from "@/utils/colors";
import { formatCompactNumber } from "@/utils/formatters";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { useThemeStore } from "@/hooks/use-theme";

export type DonutChartItem = {
  /** Stable unique key for React reconciliation. Falls back to label if not provided. */
  id?: string;
  label: string;
  /** Suffix appended after the label (not blurred in privacy mode). */
  labelSuffix?: string;
  /** When true the label text gets CSS-blurred in privacy mode. */
  isEmail?: boolean;
  value: number;
  color?: string;
};

export type DonutChartProps = {
  items: DonutChartItem[];
  total: number;
  centerValue?: number;
  title: string;
  subtitle?: string;
  safeLine?: { safePercent: number; riskLevel: "safe" | "warning" | "danger" | "critical" } | null;
};

function SafeLineTick({
  cx,
  cy,
  safePercent,
  riskLevel,
  innerRadius,
  outerRadius,
  isDark,
}: {
  cx: number;
  cy: number;
  safePercent: number;
  riskLevel: "safe" | "warning" | "danger" | "critical";
  innerRadius: number;
  outerRadius: number;
  isDark: boolean;
}) {
  if (riskLevel === "safe") return null;

  const remainingBudget = 100 - safePercent;
  const angleDeg = 90 - (remainingBudget / 100) * 360;
  const angleRad = -(angleDeg * Math.PI) / 180;

  const x1 = cx + innerRadius * Math.cos(angleRad);
  const y1 = cy + innerRadius * Math.sin(angleRad);
  const x2 = cx + outerRadius * Math.cos(angleRad);
  const y2 = cy + outerRadius * Math.sin(angleRad);

  return (
    <line
      x1={x1}
      y1={y1}
      x2={x2}
      y2={y2}
      stroke={isDark ? "#ffffff" : "#000000"}
      strokeWidth={2}
      strokeLinecap="round"
      data-testid="safe-line-tick"
    />
  );
}

const CHART_SIZE = 152;
const CHART_MARGIN = 4;
const PIE_CX = 72;
const PIE_CY = 72;
const INNER_R = 53;
const OUTER_R = 68;
const ACTIVE_RADIUS_OFFSET = 4;
const LEGEND_VISIBLE_COUNT = 4;
const LEGEND_ROW_HEIGHT_REM = 1.75;
const LEGEND_ROW_GAP_REM = 0.625;

type DonutDatum = {
  id: string;
  name: string;
  value: number;
  fill: string;
};

function formatUsedPercent(percent: number): string {
  if (!Number.isFinite(percent) || percent <= 0) {
    return "0%";
  }

  const maximumFractionDigits = percent < 10 ? 1 : 0;
  return `${percent.toLocaleString("en-US", { maximumFractionDigits })}%`;
}

export function DonutChart({ items, total, centerValue, title, subtitle, safeLine }: DonutChartProps) {
  const isDark = useThemeStore((s) => s.theme === "dark");
  const blurred = usePrivacyStore((s) => s.blurred);
  const reducedMotion = useReducedMotion();
  const [activeLegendId, setActiveLegendId] = useState<string | null>(null);
  const legendRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const consumedColor = isDark ? "#404040" : "#d3d3d3";
  const palette = buildDonutPalette(items.length, isDark);
  const normalizedItems = items.map((item, index) => ({
    ...item,
    color: item.color ?? palette[index % palette.length],
  }));

  const usedSum = normalizedItems.reduce((acc, item) => acc + Math.max(0, item.value), 0);
  const safeCapacity = Math.max(0, total);
  const consumed = Math.max(0, total - usedSum);
  const displayTotal = Math.max(0, centerValue ?? total);
  const usedPercent = safeCapacity > 0 ? (consumed / safeCapacity) * 100 : 0;

  const chartData: DonutDatum[] = [
    ...normalizedItems.map((item) => ({
      id: item.id ?? item.label,
      name: item.label,
      value: Math.max(0, item.value),
      fill: item.color,
    })),
    ...(consumed > 0
      ? [{ id: "__consumed__", name: "__consumed__", value: consumed, fill: consumedColor }]
      : []),
  ];

  const hasData = chartData.some((d) => d.value > 0);
  if (!hasData) {
    chartData.length = 0;
    chartData.push({ id: "__empty__", name: "__empty__", value: 1, fill: consumedColor });
  }

  useEffect(() => {
    if (!activeLegendId) {
      return;
    }

    legendRefs.current[activeLegendId]?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activeLegendId]);

  const renderDonutShape = (props: PieSectorShapeProps) => {
    const isHighlighted = props.isActive || (props.payload as DonutDatum | undefined)?.id === activeLegendId;
    const outerRadius = typeof props.outerRadius === "number"
      ? props.outerRadius + (isHighlighted ? ACTIVE_RADIUS_OFFSET : 0)
      : OUTER_R + (isHighlighted ? ACTIVE_RADIUS_OFFSET : 0);

    return (
      <Sector
        {...props}
        outerRadius={outerRadius}
        stroke={isHighlighted ? "hsl(var(--background))" : "none"}
        strokeWidth={isHighlighted ? 2 : 0}
      />
    );
  };

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="mb-5">
        <h3 className="text-sm font-semibold">{title}</h3>
        {subtitle ? <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p> : null}
      </div>

      <div className="flex items-center gap-6">
        <div className="flex shrink-0 flex-col items-center gap-2">
          <div className="relative h-[152px] w-[152px] overflow-visible">
            <PieChart width={CHART_SIZE} height={CHART_SIZE} margin={{ top: CHART_MARGIN, right: CHART_MARGIN, bottom: CHART_MARGIN, left: CHART_MARGIN }}>
             <Pie
               data={chartData}
               cx={PIE_CX}
               cy={PIE_CY}
               innerRadius={INNER_R}
                outerRadius={OUTER_R}
                startAngle={90}
                endAngle={-270}
                dataKey="value"
                stroke="none"
                shape={renderDonutShape}
                isAnimationActive={!reducedMotion}
                animationDuration={600}
                animationEasing="ease-out"
                onMouseEnter={(data) => {
                  if (typeof data?.id === "string") {
                    setActiveLegendId(data.id);
                  }
                }}
                onMouseLeave={() => setActiveLegendId(null)}
                onMouseOut={() => setActiveLegendId(null)}
              >
                {chartData.map((entry) => (
                  <Cell key={entry.id} fill={entry.fill} />
               ))}
             </Pie>
           </PieChart>
           {safeLine && safeLine.riskLevel !== "safe" ? (
            <svg aria-hidden="true" className="pointer-events-none absolute inset-0" width={CHART_SIZE} height={CHART_SIZE} viewBox={`0 0 ${CHART_SIZE} ${CHART_SIZE}`}>
              <SafeLineTick
                cx={PIE_CX + CHART_MARGIN}
                cy={PIE_CY + CHART_MARGIN}
                safePercent={safeLine.safePercent}
                riskLevel={safeLine.riskLevel}
                innerRadius={INNER_R}
                outerRadius={OUTER_R}
                isDark={isDark}
              />
            </svg>
          ) : null}
          <div className="absolute inset-[22px] flex items-center justify-center rounded-full text-center pointer-events-none">
             <div>
               <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Remaining</p>
               <p className="text-base font-semibold tabular-nums">{formatCompactNumber(displayTotal)}</p>
            </div>
          </div>
          </div>
          <p className="text-[11px] tabular-nums text-muted-foreground" data-testid="donut-caption">
            Total {formatCompactNumber(safeCapacity)} · {formatUsedPercent(usedPercent)} used
          </p>
        </div>

        <div
          className="flex-1 space-y-2.5 overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          data-testid="donut-legend-list"
          style={{ maxHeight: `calc(${LEGEND_VISIBLE_COUNT} * ${LEGEND_ROW_HEIGHT_REM}rem + ${(LEGEND_VISIBLE_COUNT - 1) * LEGEND_ROW_GAP_REM}rem)` }}
        >
          {normalizedItems.map((item, i) => {
            const legendId = item.id ?? item.label;
            const isActive = activeLegendId === legendId;

            return (
            <button
              ref={(node) => {
                legendRefs.current[legendId] = node;
              }}
              type="button"
              key={legendId}
              className="animate-fade-in-up flex h-7 w-full items-center justify-between gap-3 rounded-lg border bg-transparent text-xs transition-all"
              style={{ animationDelay: `${i * 75}ms`, borderColor: isActive ? item.color : "transparent" }}
              onMouseEnter={() => setActiveLegendId(legendId)}
              onMouseLeave={() => setActiveLegendId(null)}
              onFocus={() => setActiveLegendId(legendId)}
              onBlur={() => setActiveLegendId(null)}
              data-active={isActive ? "true" : "false"}
              data-testid={`donut-legend-${i}`}
            >
              <div className="flex min-w-0 items-center gap-2">
                <span
                  aria-hidden
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: item.color }}
                />
                <span className="truncate font-medium">
                  {item.isEmail && blurred
                    ? <><span className="privacy-blur">{item.label}</span>{item.labelSuffix}</>
                    : <>{item.label}{item.labelSuffix}</>}
                </span>
              </div>
              <span className="tabular-nums text-muted-foreground">
                {formatCompactNumber(item.value)}
              </span>
            </button>
            );
          })}
          <button
            ref={(node) => {
              legendRefs.current.__consumed__ = node;
            }}
            type="button"
            className="flex h-7 w-full items-center justify-between gap-3 rounded-lg border bg-transparent text-xs transition-all"
            style={{ borderColor: activeLegendId === "__consumed__" ? consumedColor : "transparent" }}
            onMouseEnter={() => setActiveLegendId("__consumed__")}
            onMouseLeave={() => setActiveLegendId(null)}
            onFocus={() => setActiveLegendId("__consumed__")}
            onBlur={() => setActiveLegendId(null)}
            data-active={activeLegendId === "__consumed__" ? "true" : "false"}
            data-testid="donut-used-row"
          >
            <div className="flex min-w-0 items-center gap-2">
              <span
                aria-hidden
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: consumedColor }}
              />
              <span className="truncate font-medium">Used</span>
            </div>
            <span className="tabular-nums text-muted-foreground" data-testid="donut-used-value">
              {formatCompactNumber(consumed)}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}
