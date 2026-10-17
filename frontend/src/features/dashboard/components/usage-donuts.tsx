import { lazy, Suspense, useMemo } from "react";
import { useTranslation } from "react-i18next";

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
	unquantifiedAccountCountPrimary?: number;
	unquantifiedAccountCountSecondary?: number;
};

function UsageDonut({
	unquantifiedAccountCount,
	...props
}: DonutChartProps & { readonly unquantifiedAccountCount: number }) {
	const { t } = useTranslation();
	const subtitle = unquantifiedAccountCount > 0
		? t("dashboard.usage.excludedAccounts", { count: unquantifiedAccountCount })
		: undefined;

	if (unquantifiedAccountCount > 0 && props.total === 0) {
		return (
			<div className="min-w-0 rounded-xl border bg-card p-5" data-testid="usage-allowance-unknown">
				<h3 className="text-sm font-semibold">{props.title}</h3>
				<p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
				<p className="mt-5 text-sm font-medium">{t("dashboard.usage.allowanceUnknown")}</p>
			</div>
		);
	}

	return <DonutChart {...props} subtitle={subtitle} />;
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
	unquantifiedAccountCountPrimary = 0,
	unquantifiedAccountCountSecondary = 0,
}: UsageDonutsProps) {
	const { t } = useTranslation();
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
			<div className="grid min-w-0 gap-4 lg:grid-cols-2">
			<UsageDonut
				title={t("dashboard.usage.fiveHourCredits")}
				items={primaryChartItems}
				total={primaryTotal}
				centerValue={primaryCenterValue}
				safeLine={safeLinePrimary}
				centerLayout="credits"
				unquantifiedAccountCount={unquantifiedAccountCountPrimary}
			/>
			<UsageDonut
				title={t("dashboard.usage.weeklyCredits")}
				items={secondaryChartItems}
				total={secondaryTotal}
				centerValue={secondaryCenterValue}
				safeLine={safeLineSecondary}
				centerLayout="credits"
				unquantifiedAccountCount={unquantifiedAccountCountSecondary}
			/>
			</div>
		</Suspense>
	);
}
