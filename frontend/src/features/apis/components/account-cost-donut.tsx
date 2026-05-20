import { useMemo } from "react";
import { Cell, Pie, PieChart, Sector, type PieSectorShapeProps } from "recharts";

import { buildDonutPalette } from "@/utils/colors";
import { formatCurrency } from "@/utils/formatters";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { useThemeStore } from "@/hooks/use-theme";
import type { ApiKeyAccountCost } from "@/features/apis/schemas";

export type AccountCostDonutProps = {
  accountCosts: ApiKeyAccountCost[];
  totalCostUsd: number;
};

const CHART_SIZE = 152;
const CHART_MARGIN = 4;
const PIE_CX = 72;
const PIE_CY = 72;
const INNER_R = 53;
const OUTER_R = 68;
const ACTIVE_RADIUS_OFFSET = 4;
const MAX_LEGEND_ITEMS = 4;

type DonutDatum = {
  id: string;
  name: string;
  value: number;
  fill: string;
};

export function AccountCostDonut({ accountCosts, totalCostUsd }: AccountCostDonutProps) {
  const isDark = useThemeStore((s) => s.theme === "dark");
  const blurred = usePrivacyStore((s) => s.blurred);
  const reducedMotion = useReducedMotion();
  const consumedColor = isDark ? "#404040" : "#d3d3d3";

	const { chartData, legendItems } = useMemo(() => {
		const visibleCosts = accountCosts.filter((ac) => ac.costUsd > 0);
		const palette = buildDonutPalette(visibleCosts.length, isDark);

		const items = visibleCosts.map((ac, i) => {
			const isDeleted = ac.isDeleted;
			return {
				id: isDeleted ? "__deleted__" : (ac.accountId ?? `__unknown_${i}__`),
				label: isDeleted ? "Deleted Account" : (ac.email ?? "Unknown Account"),
				isDeleted,
				value: ac.costUsd,
				color: isDeleted ? consumedColor : palette[i % palette.length],
			};
		});

    const totalValue = items.reduce((sum, item) => sum + item.value, 0);
    const remaining = Math.max(0, totalCostUsd - totalValue);

    const data: DonutDatum[] = [
      ...items.map((item) => ({
        id: item.id,
        name: item.label,
        value: item.value,
        fill: item.color,
      })),
      ...(remaining > 0
        ? [{ id: "__remaining__", name: "__remaining__", value: remaining, fill: consumedColor }]
        : []),
    ];

    if (!data.some((d) => d.value > 0)) {
      data.length = 0;
      data.push({ id: "__empty__", name: "__empty__", value: 1, fill: consumedColor });
    }

    return { chartData: data, legendItems: items };
  }, [accountCosts, totalCostUsd, isDark, consumedColor]);

  const renderDonutShape = (props: PieSectorShapeProps) => {
    return (
      <Sector
        {...props}
        outerRadius={
          typeof props.outerRadius === "number"
            ? props.outerRadius + (props.isActive ? ACTIVE_RADIUS_OFFSET : 0)
            : OUTER_R + (props.isActive ? ACTIVE_RADIUS_OFFSET : 0)
        }
        stroke={props.isActive ? "hsl(var(--background))" : "none"}
        strokeWidth={props.isActive ? 2 : 0}
      />
    );
  };

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="mb-3">
        <h3 className="text-sm font-semibold">7-Day Cost by Account</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">Breakdown of usage cost</p>
      </div>

      <div className="flex flex-col items-center gap-2">
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
            >
              {chartData.map((entry) => (
                <Cell key={entry.id} fill={entry.fill} />
              ))}
            </Pie>
          </PieChart>
          <div className="absolute inset-[22px] flex items-center justify-center rounded-full text-center pointer-events-none">
            <div>
              <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">7-Day Cost</p>
              <p className="text-base font-semibold tabular-nums">{formatCurrency(totalCostUsd)}</p>
            </div>
          </div>
        </div>

        <p className="text-[11px] tabular-nums text-muted-foreground">
          Total {formatCurrency(totalCostUsd)}
        </p>
      </div>

      {legendItems.length > 0 && (
        <div className="mt-3 space-y-0.5">
          {legendItems.slice(0, MAX_LEGEND_ITEMS).map((item) => (
            <div
              key={item.id}
              className="flex h-7 items-center justify-between px-1.5 gap-3 text-xs"
            >
              <div className="flex min-w-0 items-center gap-2">
                <span
                  aria-hidden
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: item.color }}
                />
                <span className="truncate font-medium">
                  {item.isDeleted ? (
                    item.label
                  ) : blurred ? (
                    <span className="privacy-blur">{item.label}</span>
                  ) : (
                    item.label
                  )}
                </span>
              </div>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {formatCurrency(item.value)}
              </span>
            </div>
          ))}
          {legendItems.length > MAX_LEGEND_ITEMS && (
            <p className="px-1.5 text-[10px] text-muted-foreground">
              +{legendItems.length - MAX_LEGEND_ITEMS} more
            </p>
          )}
        </div>
      )}
    </div>
  );
}
