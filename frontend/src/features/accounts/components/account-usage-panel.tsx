import { Clock, Flame } from "lucide-react";

import { cn } from "@/lib/utils";
import { AccountTrendChart } from "@/features/accounts/components/account-trend-chart";
import type { AccountSummary, AccountTrendsResponse } from "@/features/accounts/schemas";
import { quotaBarColor, quotaBarTrack } from "@/utils/account-status";
import {
  formatCompactNumber,
  formatCurrency,
  formatPercentNullable,
  formatQuotaResetLabel,
  formatResetRelative,
  formatWindowLabel,
} from "@/utils/formatters";

export type AccountUsagePanelProps = {
  account: AccountSummary;
  trends?: AccountTrendsResponse | null;
};

function QuotaRow({
  label,
  percent,
  resetAt,
}: {
  label: string;
  percent: number | null;
  resetAt: string | null | undefined;
}) {
  const clamped = percent === null ? 0 : Math.max(0, Math.min(100, percent));
  const hasPercent = percent !== null;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium">{label} remaining</span>
        <span
          className={cn(
            "tabular-nums font-medium",
            !hasPercent
              ? "text-muted-foreground"
              : clamped >= 70
                ? "text-emerald-600 dark:text-emerald-400"
                : clamped >= 30
                  ? "text-amber-600 dark:text-amber-400"
                  : "text-red-600 dark:text-red-400",
          )}
        >
          {formatPercentNullable(percent)}
        </span>
      </div>
      <div className={cn("h-1.5 w-full overflow-hidden rounded-full", quotaBarTrack(clamped))}>
        <div
          className={cn("h-full rounded-full transition-all duration-500 ease-out", quotaBarColor(clamped))}
          style={{ width: `${clamped}%` }}
        />
      </div>
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Clock className="h-3 w-3 shrink-0" />
        <span>Reset {formatQuotaResetLabel(resetAt ?? null)}</span>
      </div>
    </div>
  );
}

const ADDITIONAL_LIMIT_LABELS: Record<string, string> = {
  codex_spark: "GPT-5.3-Codex-Spark",
  codex_other: "GPT-5.3-Codex-Spark",
  "gpt-5.3-codex-spark": "GPT-5.3-Codex-Spark",
};

function formatAdditionalLimitName(limitName: string, quotaKey?: string | null): string {
  const normalizedQuotaKey = quotaKey?.trim().toLowerCase();
  if (normalizedQuotaKey && ADDITIONAL_LIMIT_LABELS[normalizedQuotaKey]) {
    return ADDITIONAL_LIMIT_LABELS[normalizedQuotaKey];
  }
  const normalized = limitName.trim().toLowerCase();
  return ADDITIONAL_LIMIT_LABELS[normalized] ?? limitName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatResetCountdown(resetAt: number | null): string | null {
  if (resetAt === null) return null;
  const diffMs = resetAt * 1000 - Date.now();
  if (diffMs <= 0) return "Resetting...";
  return `Resets ${formatResetRelative(diffMs)}`;
}

function AdditionalQuotaRow({
  label,
  usedPercent,
  resetAt,
}: {
  label: string;
  usedPercent: number;
  resetAt: number | null;
}) {
  const clamped = Math.max(0, Math.min(100, usedPercent));
  const countdown = formatResetCountdown(resetAt);

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums font-medium">{Math.round(usedPercent)}% used</span>
      </div>
      <div className="h-1.5 rounded-full bg-muted">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            clamped > 95
              ? "bg-red-500"
              : clamped > 80
                ? "bg-orange-500"
                : clamped > 60
                  ? "bg-amber-500"
                  : "bg-green-500",
          )}
          style={{ width: `${clamped}%` }}
        />
      </div>
      {countdown ? <p className="text-[11px] text-muted-foreground">{countdown}</p> : null}
    </div>
  );
}

const ADDITIONAL_ROUTING_POLICY_LABELS: Record<string, string> = {
  burn_first: "Burn first",
  normal: "Normal",
  preserve: "Preserve",
};

export function AccountUsagePanel({ account, trends }: AccountUsagePanelProps) {
  const primary = account.usage?.primaryRemainingPercent ?? null;
  const secondary = account.usage?.secondaryRemainingPercent ?? null;
  const requestUsage = account.requestUsage ?? null;
  const hasRequestUsage = (requestUsage?.requestCount ?? 0) > 0;
  const weeklyOnly = account.windowMinutesPrimary == null && account.windowMinutesSecondary != null;
  const hasTrends = trends && (trends.primary.length > 0 || trends.secondary.length > 0);

  return (
    <div className="space-y-4 rounded-lg border bg-muted/30 p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Usage</h3>
      <div className={cn("grid gap-4", weeklyOnly ? "grid-cols-1" : "grid-cols-2")}>
        {!weeklyOnly && <QuotaRow label="5h" percent={primary} resetAt={account.resetAtPrimary} />}
        <QuotaRow label="Weekly" percent={secondary} resetAt={account.resetAtSecondary} />
      </div>
      <div className="rounded-md border bg-background/60 px-3 py-2">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Request logs total</p>
        {hasRequestUsage ? (
          <p className="mt-1 text-xs tabular-nums text-muted-foreground">
            {formatCompactNumber(requestUsage?.totalTokens)} tok | {formatCompactNumber(requestUsage?.cachedInputTokens)} cached |{" "}
            {formatCompactNumber(requestUsage?.requestCount)} req | {formatCurrency(requestUsage?.totalCostUsd)}
          </p>
        ) : (
          <p className="mt-1 text-xs text-muted-foreground">No request usage yet.</p>
        )}
      </div>
      {account.additionalQuotas.length > 0 ? (
        <div className="space-y-3">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Additional Quotas
          </p>
          {account.additionalQuotas.map((quota) => (
            <div key={quota.quotaKey ?? quota.limitName} className="rounded-md border bg-background/60 px-3 py-2 space-y-2">
              <p className="text-xs font-medium">
                <span>{quota.displayLabel ?? formatAdditionalLimitName(quota.limitName, quota.quotaKey)}</span>
                {quota.routingPolicy !== "inherit" ? (
                  <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-orange-200 bg-orange-50 px-1.5 py-0.5 text-[10px] font-medium text-orange-700 dark:border-orange-900/60 dark:bg-orange-950/40 dark:text-orange-300">
                    <Flame className="h-3 w-3" aria-hidden="true" />
                    {ADDITIONAL_ROUTING_POLICY_LABELS[quota.routingPolicy] ?? quota.routingPolicy}
                  </span>
                ) : null}
              </p>
              {quota.primaryWindow != null ? (
                <AdditionalQuotaRow
                  label={formatWindowLabel("primary", quota.primaryWindow.windowMinutes ?? null)}
                  usedPercent={quota.primaryWindow.usedPercent}
                  resetAt={quota.primaryWindow.resetAt ?? null}
                />
              ) : null}
              {quota.secondaryWindow != null ? (
                <AdditionalQuotaRow
                  label={formatWindowLabel("secondary", quota.secondaryWindow.windowMinutes ?? null)}
                  usedPercent={quota.secondaryWindow.usedPercent}
                  resetAt={quota.secondaryWindow.resetAt ?? null}
                />
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
      {hasTrends && (
        <div className="pt-3">
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">7-day trend</h4>
            <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-full bg-chart-1" />
                5h
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-full bg-chart-2" />
                Weekly
              </span>
            </div>
          </div>
          <AccountTrendChart primary={trends.primary} secondary={trends.secondary} />
        </div>
      )}
    </div>
  );
}
