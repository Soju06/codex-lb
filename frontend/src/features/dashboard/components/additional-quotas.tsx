import type { AdditionalQuotaView } from "@/features/dashboard/utils";
import { formatWindowLabel } from "@/utils/formatters";

function formatResetCountdown(resetAt: number | null): string {
	if (resetAt === null || resetAt === undefined) return "";
	const diffMs = resetAt * 1000 - Date.now();
	if (diffMs <= 0) return "Resetting...";
	const hours = Math.floor(diffMs / 3600000);
	const minutes = Math.floor((diffMs % 3600000) / 60000);
	if (hours > 0) return `Resets in ${hours}h ${minutes}m`;
	return `Resets in ${minutes}m`;
}

function usageColor(percent: number): string {
	if (percent > 95) return "bg-red-500";
	if (percent > 80) return "bg-orange-500";
	if (percent > 60) return "bg-amber-500";
	return "bg-green-500";
}

type WindowRowProps = {
	label: string;
	usedPercent: number;
	resetAt: number | null;
};

function WindowRow({ label, usedPercent, resetAt }: WindowRowProps) {
	const countdown = formatResetCountdown(resetAt);

	return (
		<div className="space-y-1.5">
			<div className="flex items-center justify-between text-xs">
				<span className="text-muted-foreground">{label}</span>
				<span className="tabular-nums font-medium">
					{Math.round(usedPercent)}% used
				</span>
			</div>
			<div className="h-1.5 rounded-full bg-muted">
				<div
					className={`h-full rounded-full transition-all ${usageColor(usedPercent)}`}
					style={{ width: `${Math.min(100, Math.max(0, usedPercent))}%` }}
				/>
			</div>
			{countdown ? (
				<p className="text-[11px] text-muted-foreground">{countdown}</p>
			) : null}
		</div>
	);
}

export type AdditionalQuotasProps = {
	items: AdditionalQuotaView[];
};

export function AdditionalQuotas({ items }: AdditionalQuotasProps) {
	if (items.length === 0) return null;

	return (
		<section className="space-y-4">
			<div className="flex items-center gap-3">
				<h2 className="text-[13px] font-medium uppercase tracking-wider text-muted-foreground">
					Additional Quotas
				</h2>
				<div className="h-px flex-1 bg-border" />
			</div>

			<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
				{items.map((item) => (
					<div
						key={item.limitName}
						className="rounded-xl border bg-card p-5 space-y-3"
					>
						<h3 className="text-sm font-semibold">{item.displayName}</h3>

						{item.primaryUsedPercent != null ? (
							<WindowRow
								label={formatWindowLabel("primary", item.primaryWindowMinutes)}
								usedPercent={item.primaryUsedPercent}
								resetAt={item.primaryResetAt}
							/>
						) : null}

						{item.secondaryUsedPercent != null ? (
							<WindowRow
								label={formatWindowLabel(
									"secondary",
									item.secondaryWindowMinutes,
								)}
								usedPercent={item.secondaryUsedPercent}
								resetAt={item.secondaryResetAt}
							/>
						) : null}
					</div>
				))}
			</div>
		</section>
	);
}
