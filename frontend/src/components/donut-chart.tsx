import { buildDonutGradient, buildDonutPalette } from "@/utils/colors";
import { formatCompactNumber } from "@/utils/formatters";

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
  const palette = buildDonutPalette(items.length);
  const normalizedItems = items.map((item, index) => ({
    ...item,
    color: item.color ?? palette[index % palette.length],
  }));
  const gradient = buildDonutGradient(normalizedItems, total);
  const safeTotal = Math.max(0, total);

  return (
    <div className="rounded-xl border bg-card p-4">
      <div className="mb-4">
        <h3 className="text-sm font-semibold">{title}</h3>
        {subtitle ? <p className="text-xs text-muted-foreground">{subtitle}</p> : null}
      </div>

      <div className="flex items-center gap-4">
        <div className="relative h-32 w-32 shrink-0">
          <div className="h-full w-full rounded-full" style={{ backgroundImage: gradient }} />
          <div className="absolute inset-5 flex items-center justify-center rounded-full border bg-background text-center">
            <div>
              <p className="text-xs text-muted-foreground">Remaining</p>
              <p className="text-sm font-semibold">{formatCompactNumber(safeTotal)}</p>
            </div>
          </div>
        </div>

        <div className="flex-1 space-y-2">
          {normalizedItems.map((item) => (
            <div key={item.label} className="flex items-center justify-between gap-3 text-xs">
              <div className="flex min-w-0 items-center gap-2">
                <span
                  aria-hidden
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: item.color }}
                />
                <span className="truncate">{item.label}</span>
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
