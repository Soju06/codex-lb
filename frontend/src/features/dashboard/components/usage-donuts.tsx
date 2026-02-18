import { useMemo } from "react";

import { DonutChart } from "@/components/donut-chart";
import type { RemainingItem } from "@/features/dashboard/utils";
import { formatWindowLabel } from "@/utils/formatters";

export type UsageDonutsProps = {
  primaryItems: RemainingItem[];
  secondaryItems: RemainingItem[];
  primaryTotal: number;
  secondaryTotal: number;
  primaryWindowMinutes: number | null;
  secondaryWindowMinutes: number | null;
};

export function UsageDonuts({
  primaryItems,
  secondaryItems,
  primaryTotal,
  secondaryTotal,
  primaryWindowMinutes,
  secondaryWindowMinutes,
}: UsageDonutsProps) {
  const primaryChartItems = useMemo(
    () => primaryItems.map((item) => ({ label: item.label, value: item.value, color: item.color })),
    [primaryItems],
  );
  const secondaryChartItems = useMemo(
    () => secondaryItems.map((item) => ({ label: item.label, value: item.value, color: item.color })),
    [secondaryItems],
  );

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <DonutChart
        title="Primary Remaining"
        subtitle={`Window ${formatWindowLabel("primary", primaryWindowMinutes)}`}
        items={primaryChartItems}
        total={primaryTotal}
      />
      <DonutChart
        title="Secondary Remaining"
        subtitle={`Window ${formatWindowLabel("secondary", secondaryWindowMinutes)}`}
        items={secondaryChartItems}
        total={secondaryTotal}
      />
    </div>
  );
}
