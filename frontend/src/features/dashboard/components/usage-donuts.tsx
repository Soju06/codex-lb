import { lazy, Suspense, useMemo } from "react";

import type { DonutChartProps } from "@/components/donut-chart";
import type { RemainingItem, SafeLineView } from "@/features/dashboard/utils";

const DonutChart = lazy(() =>
  import("@/components/donut-chart").then((module) => ({
    default: (props: DonutChartProps) => <module.DonutChart {...props} />,
  })),
);

export type UsageDonutsProps = {
	primaryItems: RemainingItem[];
	secondaryItems: RemainingItem[];
	primaryTotal: number;
	secondaryTotal: number;
	primaryCenterValue?: number;
	secondaryCenterValue?: number;
	safeLinePrimary?: SafeLineView | null;
	safeLineSecondary?: SafeLineView | null;
};

export function UsageDonuts({
	primaryItems,
	secondaryItems,
	primaryTotal,
	secondaryTotal,
	primaryCenterValue,
	secondaryCenterValue,
	safeLinePrimary,
	safeLineSecondary,
}: UsageDonutsProps) {
	const primaryChartItems = useMemo(
		() =>
			primaryItems.map((item) => ({
				id: item.accountId,
				label: item.label,
				labelSuffix: item.labelSuffix,
				isEmail: item.isEmail,
				value: item.value,
				color: item.color,
			})),
		[primaryItems],
	);
	const secondaryChartItems = useMemo(
		() =>
			secondaryItems.map((item) => ({
				id: item.accountId,
				label: item.label,
				labelSuffix: item.labelSuffix,
				isEmail: item.isEmail,
				value: item.value,
				color: item.color,
			})),
		[secondaryItems],
	);

	return (
		<Suspense fallback={<div className="grid gap-4 lg:grid-cols-2" />}>
			<div className="grid gap-4 lg:grid-cols-2">
			<DonutChart
				title="5-Hour Credits"
				items={primaryChartItems}
				total={primaryTotal}
				centerValue={primaryCenterValue}
				safeLine={safeLinePrimary}
				centerLayout="credits"
			/>
			<DonutChart
				title="Weekly Credits"
				items={secondaryChartItems}
				total={secondaryTotal}
				centerValue={secondaryCenterValue}
				safeLine={safeLineSecondary}
				centerLayout="credits"
			/>
			</div>
		</Suspense>
	);
}
