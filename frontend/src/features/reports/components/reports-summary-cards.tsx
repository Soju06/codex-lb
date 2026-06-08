import type { ReportSummary } from "../schemas";

export type ReportsSummaryCardsProps = {
  summary: ReportSummary;
};

export function ReportsSummaryCards({ summary }: ReportsSummaryCardsProps) {
  const cards = [
    {
      label: "Custo Total",
      value: `$${summary.totalCostUsd.toFixed(2)}`,
      sub: `média $${summary.avgCostPerDay.toFixed(2)}/dia`,
    },
    {
      label: "Tokens",
      value: formatNumber(summary.totalInputTokens + summary.totalOutputTokens),
      sub: `Input ${formatNumber(summary.totalInputTokens)} · Output ${formatNumber(summary.totalOutputTokens)}`,
    },
    {
      label: "Requisições",
      value: formatNumber(summary.totalRequests),
      sub: `média ${summary.avgRequestsPerDay.toFixed(0)}/dia · ${summary.activeAccounts} contas`,
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {cards.map((card) => (
        <div key={card.label} className="rounded-xl border bg-card p-4">
          <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            {card.label}
          </div>
          <div className="mt-1 text-[1.625rem] font-semibold tracking-[-0.02em] text-foreground">
            {card.value}
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">{card.sub}</div>
        </div>
      ))}
    </div>
  );
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
