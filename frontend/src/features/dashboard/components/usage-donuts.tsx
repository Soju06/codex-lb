import { useMemo } from "react";

import { DonutChart } from "@/components/donut-chart";
import type { RemainingItem, SafeLineView } from "@/features/dashboard/utils";
import { formatWindowLabel } from "@/utils/formatters";

export type UsageDonutsProps = {
	primaryItems: RemainingItem[];
	secondaryItems: RemainingItem[];
	primaryTotal: number;
	secondaryTotal: number;
	primaryWindowMinutes: number | null;
	secondaryWindowMinutes: number | null;
	safeLine?: SafeLineView | null;
};

export function UsageDonuts({
	primaryItems,
	secondaryItems,
	primaryTotal,
	secondaryTotal,
	primaryWindowMinutes,
	secondaryWindowMinutes,
	safeLine,
}: UsageDonutsProps) {
	const primaryChartItems = useMemo(
		() =>
			primaryItems.map((item) => ({
				label: item.label,
				value: item.value,
				color: item.color,
			})),
		[primaryItems],
	);
	const secondaryChartItems = useMemo(
		() =>
			secondaryItems.map((item) => ({
				label: item.label,
				value: item.value,
				color: item.color,
			})),
		[secondaryItems],
	);

	// The backend computes depletion per window and reports which window drives
	// the highest risk via safeLine.window.  Route the marker to the matching donut.
	// Fallback: if no window is specified, use primary unless the primary donut is empty.
	const safeLineWindow = safeLine?.window ?? (primaryItems.length === 0 || primaryTotal === 0 ? "secondary" : "primary");

	return (
		<div className="grid gap-4 lg:grid-cols-2">
			<DonutChart
				title="Primary Remaining"
				subtitle={`Window ${formatWindowLabel("primary", primaryWindowMinutes)}`}
				items={primaryChartItems}
				total={primaryTotal}
				safeLine={safeLineWindow === "primary" ? safeLine : undefined}
			/>
			<DonutChart
				title="Secondary Remaining"
				subtitle={`Window ${formatWindowLabel("secondary", secondaryWindowMinutes)}`}
				items={secondaryChartItems}
				total={secondaryTotal}
				safeLine={safeLineWindow === "secondary" ? safeLine : undefined}
			/>
		</div>
	);
}
