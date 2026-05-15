import { useMemo, useState } from "react";
import { Cell, Pie, PieChart, Sector, type PieSectorShapeProps } from "recharts";

import { usePrivacyStore } from "@/hooks/use-privacy";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { useThemeStore } from "@/hooks/use-theme";
import { buildDonutPalette } from "@/utils/colors";
import { formatCurrency } from "@/utils/formatters";
import type { ApiKeyAccountUsage7DayItem } from "@/features/apis/schemas";

type ApiAccountCostDonutProps = {
	accounts: ApiKeyAccountUsage7DayItem[];
	loading?: boolean;
	error?: string | null;
};

type ChartDatum = {
	id: string;
	name: string;
	value: number;
	fill: string;
};

const CHART_SIZE = 152;
const CHART_MARGIN = 4;
const PIE_CX = 72;
const PIE_CY = 72;
const INNER_RADIUS = 53;
const OUTER_RADIUS = 68;
const ACTIVE_RADIUS_OFFSET = 4;
const LEGEND_VISIBLE_COUNT = 3;
const DELETED_ACCOUNTS_LABEL = "Deleted Accounts";

function getAccountCostDatumId(account: ApiKeyAccountUsage7DayItem, index: number) {
	return account.accountId ?? `__unknown_account__:${index}:${account.displayName}`;
}

function sortAccountsForDonut(accounts: ApiKeyAccountUsage7DayItem[]) {
	const known = accounts
		.filter((account) => account.accountId !== null)
		.sort((a, b) => b.totalCostUsd - a.totalCostUsd);
	const unknown = accounts.filter((account) => account.accountId === null);
	return [...known, ...unknown];
}

export function ApiAccountCostDonut({
	accounts,
	loading = false,
	error = null,
}: ApiAccountCostDonutProps) {
	const isDark = useThemeStore((s) => s.theme === "dark");
	const blurred = usePrivacyStore((s) => s.blurred);
	const reducedMotion = useReducedMotion();
	const usedColor = isDark ? "#404040" : "#d3d3d3";
	const [activeId, setActiveId] = useState<string | null>(null);
	const sortedAccounts = useMemo(() => sortAccountsForDonut(accounts), [accounts]);
	const palette = buildDonutPalette(sortedAccounts.length, isDark);
	const totalCost = sortedAccounts.reduce(
		(total, account) => total + Math.max(0, account.totalCostUsd),
		0,
	);
	const chartData = sortedAccounts
		.filter((account) => account.totalCostUsd > 0)
		.map<ChartDatum>((account, index) => ({
			id: getAccountCostDatumId(account, index),
			name: account.displayName,
			value: account.totalCostUsd,
			fill:
				account.displayName === DELETED_ACCOUNTS_LABEL
					? usedColor
					: palette[index % palette.length],
		}));
	const legendData = chartData.slice(0, LEGEND_VISIBLE_COUNT);

	const hasData = chartData.length > 0;

	const renderDonutShape = (props: PieSectorShapeProps) => {
		const datum = props.payload as ChartDatum | undefined;
		const isActive = datum?.id === activeId;
		const outerRadius =
			typeof props.outerRadius === "number"
				? props.outerRadius + (isActive ? ACTIVE_RADIUS_OFFSET : 0)
				: OUTER_RADIUS + (isActive ? ACTIVE_RADIUS_OFFSET : 0);

		return (
			<Sector
				{...props}
				outerRadius={outerRadius}
				stroke={isActive ? "hsl(var(--background))" : "none"}
				strokeWidth={isActive ? 2 : 0}
			/>
		);
	};

	if (loading) {
		return (
			<div className="flex min-h-[280px] items-center justify-center text-xs text-muted-foreground">
				Loading account cost...
			</div>
		);
	}

	if (error) {
		return (
			<div className="flex min-h-[280px] items-center justify-center text-xs text-destructive">
				Account cost unavailable
			</div>
		);
	}

	if (!hasData) {
		return (
			<div className="flex min-h-[280px] items-center justify-center text-xs text-muted-foreground">
				No account cost data
			</div>
		);
	}

	return (
		<div
			className="flex min-h-[280px] flex-col items-center justify-center gap-3"
			data-testid="api-account-cost-donut"
		>
			<div className="relative h-[152px] w-[152px] shrink-0 overflow-visible">
				<PieChart
					width={CHART_SIZE}
					height={CHART_SIZE}
					margin={{ top: CHART_MARGIN, right: CHART_MARGIN, bottom: CHART_MARGIN, left: CHART_MARGIN }}
				>
					<Pie
						data={chartData}
						cx={PIE_CX}
						cy={PIE_CY}
						innerRadius={INNER_RADIUS}
						outerRadius={OUTER_RADIUS}
						startAngle={90}
						endAngle={-270}
						dataKey="value"
						stroke="none"
						shape={renderDonutShape}
						isAnimationActive={!reducedMotion}
						animationDuration={600}
						animationEasing="ease-out"
						onMouseEnter={(data) => {
							if (typeof data?.id === "string") {
								setActiveId(data.id);
							}
						}}
						onMouseLeave={() => setActiveId(null)}
					>
						{chartData.map((entry) => (
							<Cell key={entry.id} fill={entry.fill} />
						))}
					</Pie>
				</PieChart>
				<div className="pointer-events-none absolute inset-[22px] flex items-center justify-center rounded-full text-center">
					<div>
						<p className="text-[10px] font-medium uppercase leading-tight text-muted-foreground">
							7-day cost
						</p>
						<p className="text-base font-semibold leading-tight tabular-nums">
							{formatCurrency(totalCost)}
						</p>
					</div>
				</div>
			</div>

			<div className="w-full min-w-0 space-y-0.5">
				{legendData.map((entry, index) => {
					const isActive = entry.id === activeId;
					const shouldBlur = blurred && entry.name.includes("@");
					return (
						<button
							key={entry.id}
							type="button"
							className="flex min-h-6 w-full items-center justify-between gap-2 rounded-md border px-1.5 py-0.5 text-left text-xs transition-all"
							style={{ borderColor: isActive ? entry.fill : "transparent" }}
							onMouseEnter={() => setActiveId(entry.id)}
							onMouseLeave={() => setActiveId(null)}
							onFocus={() => setActiveId(entry.id)}
							onBlur={() => setActiveId(null)}
							data-testid={`api-account-cost-legend-${index}`}
						>
							<span className="flex min-w-0 items-center gap-2">
								<span
									aria-hidden
									className="h-2.5 w-2.5 shrink-0 rounded-full"
									style={{ backgroundColor: entry.fill }}
								/>
								<span className={shouldBlur ? "truncate font-medium privacy-blur" : "truncate font-medium"}>
									{entry.name}
								</span>
							</span>
							<span className="shrink-0 text-right tabular-nums text-muted-foreground">
								{formatCurrency(entry.value)}
							</span>
						</button>
					);
				})}
			</div>
		</div>
	);
}
