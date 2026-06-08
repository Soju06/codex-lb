import type { TooltipContentProps } from "recharts";

export type ChartTooltipProps = Partial<TooltipContentProps<number, string>> & {
  formatValue?: (value: number, dataKey: string) => string;
  names?: Record<string, string>;
};

export function ChartTooltip({
  active,
  payload,
  label,
  formatValue,
  names,
}: ChartTooltipProps) {
  if (!active || !payload?.length) return null;

  return (
    <div className="rounded-xl border border-border bg-popover p-2.5 shadow-md">
      {label != null && (
        <p className="mb-1.5 px-2 text-[11px] font-medium text-muted-foreground">
          {label}
        </p>
      )}
      <div className="space-y-1">
        {payload.map((entry, i) => {
          const color = entry.color ?? "#888";
          const rawName = String(entry.name ?? entry.dataKey ?? "");
          const name = names?.[rawName] ?? rawName;
          const value =
            typeof entry.value === "number"
              ? formatValue
                ? formatValue(entry.value, String(entry.dataKey))
                : String(entry.value)
              : String(entry.value);

          return (
            <div
              key={String(entry.dataKey ?? i)}
              className="flex items-center gap-2 rounded-lg px-2.5 py-1.5"
              style={{ backgroundColor: `${color}18` }}
            >
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: color }}
              />
              <span className="text-xs text-popover-foreground">{name}</span>
              <span className="ml-auto pl-2 text-xs font-semibold text-popover-foreground">
                {value}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
