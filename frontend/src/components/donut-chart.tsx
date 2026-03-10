import { Cell, Pie, PieChart } from "recharts";

import { buildDonutPalette } from "@/utils/colors";
import { formatCompactNumber } from "@/utils/formatters";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { useThemeStore } from "@/hooks/use-theme";

export type DonutChartItem = {
  label: string;
  value: number;
  color?: string;
};

export type DonutChartProps = {
  items: DonutChartItem[];
  total: number;
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
}: {
  cx: number;
  cy: number;
  safePercent: number;
  riskLevel: "safe" | "warning" | "danger" | "critical";
  innerRadius: number;
  outerRadius: number;
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
      stroke="#ffffff"
      strokeWidth={2}
      strokeLinecap="round"
      data-testid="safe-line-tick"
    />
  );
}

const CHART_SIZE = 144;
const CHART_MARGIN = 1;
const PIE_CX = 71;
const PIE_CY = 71;
const INNER_R = 53;
const OUTER_R = 71;

export function DonutChart({ items, total, title, subtitle, safeLine }: DonutChartProps) {
  const isDark = useThemeStore((s) => s.theme === "dark");
  const reducedMotion = useReducedMotion();
  const consumedColor = isDark ? "#404040" : "#d3d3d3";
  const palette = buildDonutPalette(items.length, isDark);
  const normalizedItems = items.map((item, index) => ({
    ...item,
    color: item.color ?? palette[index % palette.length],
  }));

  const usedSum = normalizedItems.reduce((acc, item) => acc + Math.max(0, item.value), 0);
  const consumed = Math.max(0, total - usedSum);
  const safeTotal = Math.max(0, total);

  const chartData = [
    ...normalizedItems.map((item) => ({
      name: item.label,
      value: Math.max(0, item.value),
      fill: item.color,
    })),
    ...(consumed > 0
      ? [{ name: "__consumed__", value: consumed, fill: consumedColor }]
      : []),
  ];

  const hasData = chartData.some((d) => d.value > 0);
  if (!hasData) {
    chartData.length = 0;
    chartData.push({ name: "__empty__", value: 1, fill: consumedColor });
  }

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="mb-5">
        <h3 className="text-sm font-semibold">{title}</h3>
        {subtitle ? <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p> : null}
      </div>

      <div className="flex items-center gap-6">
        <div className="relative h-36 w-36 shrink-0 overflow-visible">
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
              isAnimationActive={!reducedMotion}
              animationDuration={600}
              animationEasing="ease-out"
            >
              {chartData.map((entry, index) => (
                <Cell key={index} fill={entry.fill} />
              ))}
            </Pie>
          </PieChart>
          {safeLine && safeLine.riskLevel !== "safe" ? (
            <svg className="pointer-events-none absolute inset-0" width={CHART_SIZE} height={CHART_SIZE} viewBox={`0 0 ${CHART_SIZE} ${CHART_SIZE}`}>
              <SafeLineTick
                cx={PIE_CX + CHART_MARGIN}
                cy={PIE_CY + CHART_MARGIN}
                safePercent={safeLine.safePercent}
                riskLevel={safeLine.riskLevel}
                innerRadius={INNER_R}
                outerRadius={OUTER_R}
              />
            </svg>
          ) : null}
          <div className="absolute inset-[18px] flex items-center justify-center rounded-full text-center pointer-events-none">
            <div>
              <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Remaining</p>
              <p className="text-base font-semibold tabular-nums">{formatCompactNumber(safeTotal)}</p>
            </div>
          </div>
        </div>

        <div className="flex-1 space-y-2.5">
          {normalizedItems.map((item, i) => (
            <div key={item.label} className="animate-fade-in-up flex items-center justify-between gap-3 text-xs" style={{ animationDelay: `${i * 75}ms` }}>
              <div className="flex min-w-0 items-center gap-2">
                <span
                  aria-hidden
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: item.color }}
                />
                <span className="truncate font-medium">{item.label}</span>
              </div>
              <span className="tabular-nums text-muted-foreground">
                {formatCompactNumber(item.value)}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
