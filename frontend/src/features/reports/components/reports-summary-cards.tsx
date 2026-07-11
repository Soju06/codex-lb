import { cn } from "@/lib/utils";

import type { ReportComparison, ReportSummary } from "../schemas";

const COMPARISON_STYLES = {
  positive: "text-emerald-600 dark:text-emerald-400",
  negative: "text-red-600 dark:text-red-400",
} as const;

export type ReportsSummaryCardsProps = {
  summary: ReportSummary;
  comparison: ReportComparison;
};

export function ReportsSummaryCards({ summary, comparison }: ReportsSummaryCardsProps) {
  const cards = [
    {
      label: "Total Cost",
      value: `$${summary.totalCostUsd.toFixed(2)}`,
      sub: `avg $${summary.avgCostPerDay.toFixed(2)}/day`,
      comparison: buildComparison(summary.totalCostUsd, comparison.previous.totalCostUsd, comparison.canCompare),
    },
    {
      label: "Tokens",
      value: formatNumber(summary.totalInputTokens + summary.totalOutputTokens),
      sub: `Input ${formatNumber(summary.totalInputTokens)} · Cache ${formatNumber(summary.totalCachedTokens)} · Output ${formatNumber(summary.totalOutputTokens)}`,
      comparison: buildComparison(
        summary.totalInputTokens + summary.totalOutputTokens,
        comparison.previous.totalTokens,
        comparison.canCompare,
      ),
    },
    {
      label: "Requests",
      value: formatNumber(summary.totalRequests),
      sub: `avg ${summary.avgRequestsPerDay.toFixed(0)}/day · ${summary.activeAccounts} accounts`,
      comparison: buildComparison(summary.totalRequests, comparison.previous.totalRequests, comparison.canCompare),
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {cards.map((card) => (
        <div
          key={card.label}
          data-testid={`report-summary-card-${card.label}`}
          className="rounded-xl border bg-card p-4"
        >
          <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            {card.label}
          </div>
          <div className="mt-1 flex items-baseline gap-2">
            <div className="text-[1.625rem] font-semibold tracking-[-0.02em] text-foreground">
              {card.value}
            </div>
            {card.comparison ? (
              <div className={cn("text-xs font-medium", COMPARISON_STYLES[card.comparison.tone])}>
                {card.comparison.text}
              </div>
            ) : null}
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">{card.sub}</div>
        </div>
      ))}
    </div>
  );
}

function formatNumber(n: number): string {
  if (n >= 1_000_000_000) return formatCompactNumber(n / 1_000_000_000, "B");
  if (n >= 1_000_000) return formatCompactNumber(n / 1_000_000, "M");
  if (n >= 1_000) return formatCompactNumber(n / 1_000, "K");
  return String(n);
}

function formatCompactNumber(value: number, suffix: "B" | "M" | "K"): string {
  if (suffix === "M" && value >= 100 && Number.isInteger(value)) {
    return `${value.toFixed(0)}${suffix}`;
  }

  return `${value.toFixed(1)}${suffix}`;
}

function buildComparison(
  current: number,
  previous: number,
  canCompare: boolean,
): { text: string; tone: keyof typeof COMPARISON_STYLES } | undefined {
  if (!canCompare || previous <= 0) {
    return undefined;
  }

  const deltaPercent = ((current - previous) / previous) * 100;
  const roundedPercent = Math.round(Math.abs(deltaPercent));

  if (roundedPercent === 0) {
    return undefined;
  }
  if (deltaPercent > 0) {
    return { text: `▲ ${roundedPercent}%`, tone: "positive" };
  }

  return { text: `▼ ${roundedPercent}%`, tone: "negative" };
}
