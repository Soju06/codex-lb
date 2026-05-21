import {
	Ellipsis,
	KeyRound,
	Pencil,
	Play,
	RefreshCw,
	Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Switch } from "@/components/ui/switch";
import type { ApiKey } from "@/features/api-keys/schemas";
import { AccountCostDonut } from "@/features/apis/components/account-cost-donut";
import { ApiKeyInfo } from "@/features/apis/components/api-key-info";
import { ApiTrendChart } from "@/features/apis/components/api-trend-chart";
import type { ApiKeyUsage7DayResponse } from "@/features/apis/schemas";

export type ApiDetailProps = {
	apiKey: ApiKey | null;
	trends?: {
		cost: { t: string; v: number }[];
		tokens: { t: string; v: number }[];
	} | null;
	usage7Day?: ApiKeyUsage7DayResponse | null;
	usage7DayLoading?: boolean;
	usage7DayError?: string | null;
	busy: boolean;
	onEdit: (apiKey: ApiKey) => void;
	onDelete: (apiKey: ApiKey) => void;
	onRegenerate: (apiKey: ApiKey) => void;
	onToggleActive: (apiKey: ApiKey) => void;
};

function accumulateData(
	data: { t: string; v: number }[],
): { t: string; v: number }[] {
	let sum = 0;
	return data.map((point) => {
		sum += point.v;
		return { t: point.t, v: sum };
	});
}

export function ApiDetail({
	apiKey,
	trends,
	usage7Day,
	usage7DayLoading = false,
	usage7DayError = null,
	busy,
	onEdit,
	onDelete,
	onRegenerate,
	onToggleActive,
}: ApiDetailProps) {
	const [showAccumulated, setShowAccumulated] = useState(false);

	const chartData = useMemo(() => {
		if (!trends) return null;
		if (!showAccumulated) return trends;
		return {
			cost: accumulateData(trends.cost),
			tokens: accumulateData(trends.tokens),
		};
	}, [trends, showAccumulated]);

	const usageSummary = useMemo(() => {
		if (!usage7Day) return null;
		return {
			requestCount: usage7Day.totalRequests,
			totalTokens: usage7Day.totalTokens,
			cachedInputTokens: usage7Day.cachedInputTokens,
			totalCostUsd: usage7Day.totalCostUsd,
		};
	}, [usage7Day]);

	const usageMessage = useMemo(() => {
		if (usage7Day) return null;
		if (usage7DayLoading) return "Loading 7-day usage...";
		if (usage7DayError) return "7-day usage unavailable";
		return null;
	}, [usage7Day, usage7DayError, usage7DayLoading]);

	const hasDonutData = usage7Day && usage7Day.accountCosts.length > 0;
	const hasTrends = trends && (trends.cost.length > 0 || trends.tokens.length > 0);

	if (!apiKey) {
		return (
			<div className="flex flex-col items-center justify-center rounded-xl border border-dashed p-12">
				<div className="flex h-12 w-12 items-center justify-center rounded-xl bg-muted">
					<KeyRound className="h-5 w-5 text-muted-foreground" />
				</div>
				<p className="mt-3 text-sm font-medium text-muted-foreground">
					Select an API key
				</p>
				<p className="mt-1 text-xs text-muted-foreground/70">
					Choose an API key from the list to view details.
				</p>
			</div>
		);
	}

	return (
		<div
			key={apiKey.id}
			className="animate-fade-in-up space-y-4 rounded-xl border bg-card p-5"
		>
			<div className="flex items-start justify-between">
				<h2 className="text-base font-semibold">{apiKey.name}</h2>
				<DropdownMenu>
					<DropdownMenuTrigger asChild>
						<Button
							type="button"
							size="icon-sm"
							variant="ghost"
							disabled={busy}
						>
							<Ellipsis className="size-4" />
							<span className="sr-only">Actions</span>
						</Button>
					</DropdownMenuTrigger>
					<DropdownMenuContent align="end">
						<DropdownMenuItem onClick={() => onEdit(apiKey)}>
							<Pencil className="size-4" />
							Edit
						</DropdownMenuItem>
						<DropdownMenuItem onClick={() => onRegenerate(apiKey)}>
							<RefreshCw className="size-4" />
							Regenerate
						</DropdownMenuItem>
					</DropdownMenuContent>
				</DropdownMenu>
			</div>

			{hasDonutData || hasTrends ? (
				<div
					className="rounded-xl border bg-card p-4 lg:flex lg:items-start"
					data-testid="api-usage-panel"
				>
					{hasDonutData && (
						<div className={hasTrends ? "lg:w-[25%] lg:shrink-0 lg:pr-4" : "lg:w-full"}>
							<AccountCostDonut
								accountCosts={usage7Day.accountCosts}
								totalCostUsd={usage7Day.totalCostUsd}
							/>
						</div>
					)}
					{hasTrends ? (
						<div
							className={
								hasDonutData
									? "mt-4 border-t pt-4 lg:mt-0 lg:max-w-[75%] lg:flex-1 lg:border-t-0 lg:border-l lg:pl-4 lg:pt-0"
									: ""
							}
							data-testid="api-trend-panel"
						>
							<div className="mb-3 flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
								<div>
									<h3 className="text-sm font-semibold">Usage Trend</h3>
									<p className="text-xs text-muted-foreground">7-day token and cost activity</p>
								</div>
								<div className="flex flex-wrap items-center justify-start gap-3 md:justify-end">
									<div className="flex items-center gap-3 text-[10px] text-muted-foreground" data-testid="api-trend-legend">
										<span className="flex items-center gap-1.5">
											Tokens
											<span className="inline-block h-2 w-2 rounded-full bg-chart-2" />
										</span>
										<span className="flex items-center gap-1.5">
											Cost
											<span className="inline-block h-2 w-2 rounded-full bg-chart-1" />
										</span>
									</div>
									<div className="flex items-center gap-1.5 rounded-md border px-2 py-1">
										<span id="api-trend-accumulated-label" className="text-[10px]">Accumulated</span>
										<Switch
											size="sm"
											aria-labelledby="api-trend-accumulated-label"
											checked={showAccumulated}
											onCheckedChange={setShowAccumulated}
										/>
									</div>
								</div>
							</div>
							{chartData ? (
								<ApiTrendChart cost={chartData.cost} tokens={chartData.tokens} />
							) : null}
						</div>
					) : null}
				</div>
			) : null}

			{usage7DayError ? (
				<AlertMessage variant="error">{usage7DayError}</AlertMessage>
			) : null}

			<ApiKeyInfo
				apiKey={apiKey}
				usageSummary={usageSummary}
				usageMessage={usageMessage}
				allowUsageSummaryFallback={false}
			/>

			<div className="flex flex-wrap gap-2 border-t pt-4">
				{apiKey.isActive ? (
					<Button
						type="button"
						size="sm"
						variant="outline"
						className="h-8 gap-1.5 text-xs"
						onClick={() => onToggleActive(apiKey)}
						disabled={busy}
					>
						<Ellipsis className="h-3.5 w-3.5" />
						Disable
					</Button>
				) : (
					<Button
						type="button"
						size="sm"
						className="h-8 gap-1.5 text-xs"
						onClick={() => onToggleActive(apiKey)}
						disabled={busy}
					>
						<Play className="h-3.5 w-3.5" />
						Enable
					</Button>
				)}
				<Button
					type="button"
					size="sm"
					variant="destructive"
					className="h-8 gap-1.5 text-xs"
					onClick={() => onDelete(apiKey)}
					disabled={busy}
				>
					<Trash2 className="h-3.5 w-3.5" />
					Delete
				</Button>
			</div>
		</div>
	);
}
