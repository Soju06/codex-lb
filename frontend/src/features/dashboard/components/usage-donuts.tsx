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
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DonutChart
        title="Primary Remaining"
        subtitle={`Window ${formatWindowLabel("primary", primaryWindowMinutes)}`}
        items={primaryItems.map((item) => ({
          label: item.label,
          value: item.value,
          color: item.color,
        }))}
        total={primaryTotal}
      />
      <DonutChart
        title="Secondary Remaining"
        subtitle={`Window ${formatWindowLabel("secondary", secondaryWindowMinutes)}`}
        items={secondaryItems.map((item) => ({
          label: item.label,
          value: item.value,
          color: item.color,
        }))}
        total={secondaryTotal}
      />
    </div>
  );
}
