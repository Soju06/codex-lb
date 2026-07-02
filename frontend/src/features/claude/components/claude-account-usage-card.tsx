import { Gauge } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { ClaudeAccount } from "@/features/claude/schemas";
import { formatCompactNumber } from "@/utils/formatters";

export type ClaudeAccountUsageCardProps = {
  account: ClaudeAccount;
};

type RateLimitRow = {
  label: string;
  value: string;
};

export function ClaudeAccountUsageCard({ account }: ClaudeAccountUsageCardProps) {
  const { t } = useTranslation();
  const status = account.rateLimitStatus ?? t("claude.usageCard.statusUnknown");
  const rows: RateLimitRow[] = [
    {
      label: t("claude.usageCard.requestsRemaining"),
      value: formatCompactNumber(account.rateLimitRequestsRemaining ?? null),
    },
    {
      label: t("claude.usageCard.inputTokensRemaining"),
      value: formatCompactNumber(account.rateLimitInputTokensRemaining ?? null),
    },
    {
      label: t("claude.usageCard.outputTokensRemaining"),
      value: formatCompactNumber(account.rateLimitOutputTokensRemaining ?? null),
    },
  ];

  return (
    <section
      className="space-y-3 rounded-xl border bg-card p-4"
      data-testid="claude-account-usage-card"
    >
      <header className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <Gauge className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <h3 className="text-sm font-semibold">{t("claude.usageCard.title")}</h3>
      </header>

      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {rows.map((row) => (
          <div key={row.label} className="rounded-lg border bg-muted/30 px-3 py-2">
            <dt className="text-xs text-muted-foreground">{row.label}</dt>
            <dd className="mt-1 text-base font-semibold tabular-nums">{row.value}</dd>
          </div>
        ))}
      </dl>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">{t("claude.usageCard.status")}</span>
        <Badge variant="outline">{status}</Badge>
      </div>

      <div className="rounded-lg border border-dashed bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        {t("claude.usageCard.todayTokensUnavailable")}
      </div>
    </section>
  );
}