import type { ApiKey, LimitType } from "@/features/api-keys/schemas";
import { cn } from "@/lib/utils";
import {
	formatCompactNumber,
	formatCurrency,
	formatTimeLong,
} from "@/utils/formatters";

const LIMIT_TYPE_LABEL: Record<LimitType, string> = {
	total_tokens: "Total Tokens",
	input_tokens: "Input Tokens",
	output_tokens: "Output Tokens",
	cost_usd: "Cost (USD)",
};

export type ApiKeyInfoProps = {
	apiKey: ApiKey;
	usageSummary?: ApiKey["usageSummary"] | null;
	usageMessage?: string | null;
	allowUsageSummaryFallback?: boolean;
};

function formatExpiry(value: string | null): string {
	if (!value) return "Never";
	const parsed = formatTimeLong(value);
	return `${parsed.date} ${parsed.time}`;
}

function isExpired(apiKey: ApiKey): boolean {
	if (!apiKey.expiresAt) return false;
	return new Date(apiKey.expiresAt).getTime() < Date.now();
}

export function ApiKeyInfo({
	apiKey,
	usageSummary,
	usageMessage,
	allowUsageSummaryFallback = true,
}: ApiKeyInfoProps) {
	const expired = isExpired(apiKey);
	const models = apiKey.allowedModels?.join(", ") || "All models";
	const enforcedModel = apiKey.enforcedModel || null;
	const enforcedEffort = apiKey.enforcedReasoningEffort || null;
	const usage = allowUsageSummaryFallback
		? (usageSummary ?? apiKey.usageSummary)
		: (usageSummary ?? null);
	const hasUsage = usage && usage.requestCount > 0;

	return (
		<div className="space-y-4 rounded-lg border bg-muted/30 p-4">
			<h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
				Key Details
			</h3>
			<dl className="space-y-2 text-xs">
				<div className="flex items-center justify-between gap-2">
					<dt className="text-muted-foreground">Prefix</dt>
					<dd className="font-mono font-medium">{apiKey.keyPrefix}</dd>
				</div>
				<div className="flex items-center justify-between gap-2">
					<dt className="text-muted-foreground">Models</dt>
					<dd className="text-right font-medium">{models}</dd>
				</div>
				{enforcedModel ? (
					<div className="flex items-center justify-between gap-2">
						<dt className="text-muted-foreground">Enforced Model</dt>
						<dd className="font-mono font-medium">{enforcedModel}</dd>
					</div>
				) : null}
				{enforcedEffort ? (
					<div className="flex items-center justify-between gap-2">
						<dt className="text-muted-foreground">Enforced Effort</dt>
						<dd className="font-medium">{enforcedEffort}</dd>
					</div>
				) : null}
				<div className="flex items-center justify-between gap-2">
					<dt className="text-muted-foreground">Expiry</dt>
					<dd
						className={cn(
							"font-medium",
							expired ? "text-red-600 dark:text-red-400" : "",
						)}
					>
						{expired ? "Expired" : formatExpiry(apiKey.expiresAt)}
					</dd>
				</div>
				<div className="flex items-start justify-between gap-2">
					<dt className="text-muted-foreground">Usage</dt>
					<dd className="text-right tabular-nums">
						{hasUsage ? (
							<span>
								<span className="font-medium">
									{formatCompactNumber(usage.totalTokens)} tok
								</span>
								<span className="mx-1 text-muted-foreground/40">|</span>
								<span className="font-medium">
									{formatCompactNumber(usage.cachedInputTokens)} cached
								</span>
								<span className="mx-1 text-muted-foreground/40">|</span>
								<span className="font-medium">
									{formatCompactNumber(usage.requestCount)} req
								</span>
								<span className="mx-1 text-muted-foreground/40">|</span>
								<span className="font-medium">
									{formatCurrency(usage.totalCostUsd)}
								</span>
							</span>
						) : (
							<span className="text-muted-foreground">
								{usageMessage ?? "No usage recorded"}
							</span>
						)}
					</dd>
				</div>
				<div className="space-y-1.5">
					<div className="flex items-center justify-between gap-2">
						<dt className="text-muted-foreground">Limits</dt>
						<dd className="text-right tabular-nums">
							{apiKey.limits.length > 0 ? (
								<span className="font-medium">
									{apiKey.limits.length} configured
								</span>
							) : (
								<span className="text-muted-foreground">
									No limits configured
								</span>
							)}
						</dd>
					</div>
					{apiKey.limits.map((limit) => {
						const isCost = limit.limitType === "cost_usd";
						const percent =
							limit.maxValue > 0
								? Math.min(100, (limit.currentValue / limit.maxValue) * 100)
								: 0;
						const current = isCost
							? `$${(limit.currentValue / 1_000_000).toFixed(2)}`
							: formatCompactNumber(limit.currentValue);
						const max = isCost
							? `$${(limit.maxValue / 1_000_000).toFixed(2)}`
							: formatCompactNumber(limit.maxValue);
						const modelFilter = limit.modelFilter || "all";

						return (
							<div key={limit.id} className="space-y-1 pl-2">
								<div className="flex items-center justify-between gap-2 text-xs tabular-nums">
									<span className="text-muted-foreground">
										{LIMIT_TYPE_LABEL[limit.limitType]} ({limit.limitWindow},{" "}
										{modelFilter})
									</span>
									<span className="font-medium">
										{current} / {max}
									</span>
								</div>
								<div className="h-1.5 w-full rounded-full bg-muted">
									<div
										className={cn(
											"h-full rounded-full transition-all",
											percent >= 90
												? "bg-red-500"
												: percent >= 70
													? "bg-orange-500"
													: "bg-primary",
										)}
										style={{ width: `${percent}%` }}
									/>
								</div>
							</div>
						);
					})}
				</div>
			</dl>
		</div>
	);
}
