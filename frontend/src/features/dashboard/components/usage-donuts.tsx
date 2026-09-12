import { lazy, Suspense } from "react";
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
  const chartItems = (items: RemainingItem[]): DonutChartItem[] => {
    const reserved = items.reduce((sum, item) => sum + (item.reservedValue ?? 0), 0);
    return [
      ...items.map((item) => ({
        id: item.accountId, label: item.label, labelSuffix: item.labelSuffix,
        isEmail: item.isEmail, value: item.value, color: item.color,
      })),
      ...(reserved > 0 ? [{
        id: "__reserved__", label: t("accounts.usageLimit.reserved"), value: reserved,
        color: "#888888", hatched: true,
      }] : []),
    ];
  };
  const primaryChartItems = chartItems(primaryItems);
  const secondaryChartItems = chartItems(secondaryItems);

	return (
		<Suspense fallback={<div className="grid gap-4 lg:grid-cols-2" />}>
			<div className="grid min-w-0 gap-4 lg:grid-cols-2">
			<DonutChart
				title={t("dashboard.usage.fiveHourCredits")}
                subtitle={primaryItems.some((item) => (item.reservedValue ?? 0) > 0) ? t("accounts.usageLimit.donutExplanation") : undefined}
				items={primaryChartItems}
				total={primaryTotal}
				centerValue={primaryCenterValue}
				safeLine={safeLinePrimary}
				centerLayout="credits"
			/>
			<DonutChart
				title={t("dashboard.usage.weeklyCredits")}
                subtitle={secondaryItems.some((item) => (item.reservedValue ?? 0) > 0) ? t("accounts.usageLimit.donutExplanation") : undefined}
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
