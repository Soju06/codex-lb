import { useMemo, useState } from "react";

import { DonutChart } from "@/components/donut-chart";
import { MultiSelectFilter } from "@/features/dashboard/components/filters/multi-select-filter";
import type { RemainingItem, SafeLineView } from "@/features/dashboard/utils";
import { formatSlug } from "@/utils/formatters";

const DEFAULT_ACCOUNT_STATUSES = ["active", "paused", "rate_limited", "quota_exceeded"];

function sumRemaining(items: RemainingItem[]): number {
	return items.reduce((sum, item) => sum + Math.max(0, item.value), 0);
}

function estimateCapacity(items: RemainingItem[]): number {
	return items.reduce((sum, item) => {
		if (item.remainingPercent == null || item.remainingPercent <= 0) {
			return sum + Math.max(0, item.value);
		}
		return sum + Math.max(0, item.value / (item.remainingPercent / 100));
	}, 0);
}

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
	const [statusFilters, setStatusFilters] = useState<string[]>(DEFAULT_ACCOUNT_STATUSES);
	const statusOptions = useMemo(
		() =>
			[...new Set([...DEFAULT_ACCOUNT_STATUSES, ...primaryItems.map((item) => item.status), ...secondaryItems.map((item) => item.status)])]
				.sort()
				.map((status) => ({
					value: status,
					label: formatSlug(status),
				})),
		[primaryItems, secondaryItems],
	);
	const filteredPrimaryItems = useMemo(
		() => primaryItems.filter((item) => statusFilters.includes(item.status)),
		[primaryItems, statusFilters],
	);
	const filteredSecondaryItems = useMemo(
		() => secondaryItems.filter((item) => statusFilters.includes(item.status)),
		[secondaryItems, statusFilters],
	);
	const primaryChartItems = useMemo(
		() =>
			filteredPrimaryItems.map((item) => ({
				id: item.accountId,
				label: item.label,
				labelSuffix: item.labelSuffix,
				isEmail: item.isEmail,
				value: item.value,
				color: item.color,
			})),
		[filteredPrimaryItems],
	);
	const secondaryChartItems = useMemo(
		() =>
			filteredSecondaryItems.map((item) => ({
				id: item.accountId,
				label: item.label,
				labelSuffix: item.labelSuffix,
				isEmail: item.isEmail,
				value: item.value,
				color: item.color,
			})),
		[filteredSecondaryItems],
	);
	const filteredPrimaryTotal = useMemo(() => estimateCapacity(filteredPrimaryItems), [filteredPrimaryItems]);
	const filteredSecondaryTotal = useMemo(() => estimateCapacity(filteredSecondaryItems), [filteredSecondaryItems]);
	const filteredPrimaryCenterValue = useMemo(() => sumRemaining(filteredPrimaryItems), [filteredPrimaryItems]);
	const filteredSecondaryCenterValue = useMemo(() => sumRemaining(filteredSecondaryItems), [filteredSecondaryItems]);

	return (
		<div className="space-y-3">
			<MultiSelectFilter
				label="Statuses"
				values={statusFilters}
				options={statusOptions}
				onChange={setStatusFilters}
			/>
			<div className="grid gap-4 lg:grid-cols-2">
				<DonutChart
					title="5h Remaining"
					items={primaryChartItems}
					total={filteredPrimaryItems.length === primaryItems.length ? primaryTotal : filteredPrimaryTotal}
					centerValue={filteredPrimaryItems.length === primaryItems.length ? primaryCenterValue : filteredPrimaryCenterValue}
					safeLine={safeLinePrimary}
				/>
				<DonutChart
					title="Weekly Remaining"
					items={secondaryChartItems}
					total={filteredSecondaryItems.length === secondaryItems.length ? secondaryTotal : filteredSecondaryTotal}
					centerValue={filteredSecondaryItems.length === secondaryItems.length ? secondaryCenterValue : filteredSecondaryCenterValue}
					safeLine={safeLineSecondary}
				/>
			</div>
		</div>
	);
}
