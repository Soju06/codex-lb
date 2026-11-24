import { lazy, Suspense, useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { DonutChartItem, DonutChartProps } from "@/components/donut-chart";
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

// Reserved credits are one hatched slice so they read as neither usable nor consumed.
function chartItems(items: RemainingItem[], reservedLabel: string): DonutChartItem[] {
	const reserved = items.reduce((sum, item) => sum + (item.reservedValue ?? 0), 0);
	return [
		...items.map((item) => ({
			id: item.accountId,
			label: item.label,
			labelSuffix: item.labelSuffix,
			isEmail: item.isEmail,
			value: item.value,
			color: item.color,
		})),
		...(reserved > 0
			? [{ id: "__reserved__", label: reservedLabel, value: reserved, color: "#888888", hatched: true }]
			: []),
	];
}

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
	const { t } = useTranslation();
	const reservedLabel = t("accounts.usageLimit.reserved");
	const primaryChartItems = useMemo(() => chartItems(primaryItems, reservedLabel), [primaryItems, reservedLabel]);
	const secondaryChartItems = useMemo(() => chartItems(secondaryItems, reservedLabel), [secondaryItems, reservedLabel]);
	const reserveExplanation = (items: DonutChartItem[]) =>
		items.some((item) => item.hatched) ? t("accounts.usageLimit.donutExplanation") : undefined;

	return (
		<Suspense fallback={<div className="grid gap-4 lg:grid-cols-2" />}>
			<div className="grid min-w-0 gap-4 lg:grid-cols-2">
			<DonutChart
				title={t("dashboard.usage.fiveHourCredits")}
				subtitle={reserveExplanation(primaryChartItems)}
				items={primaryChartItems}
				total={primaryTotal}
				centerValue={primaryCenterValue}
				safeLine={safeLinePrimary}
				centerLayout="credits"
			/>
			<DonutChart
				title={t("dashboard.usage.weeklyCredits")}
				subtitle={reserveExplanation(secondaryChartItems)}
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
