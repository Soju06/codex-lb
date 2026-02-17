import { Cell, Pie, PieChart } from "recharts";

import { buildDonutPalette } from "@/utils/colors";
import { formatCompactNumber } from "@/utils/formatters";
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
};

export function DonutChart({ items, total, title, subtitle }: DonutChartProps) {
  const isDark = useThemeStore((s) => s.theme === "dark");
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
          <PieChart width={144} height={144} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
            <Pie
              data={chartData}
              cx={72}
              cy={72}
              innerRadius={54}
              outerRadius={72}
              startAngle={90}
              endAngle={-270}
              dataKey="value"
              stroke="none"
              isAnimationActive={true}
              animationDuration={600}
              animationEasing="ease-out"
            >
              {chartData.map((entry, index) => (
                <Cell key={index} fill={entry.fill} />
              ))}
            </Pie>
          </PieChart>
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
