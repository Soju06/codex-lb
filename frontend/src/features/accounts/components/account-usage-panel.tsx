import { Clock } from "lucide-react";

import { cn } from "@/lib/utils";
import { AccountTrendChart } from "@/features/accounts/components/account-trend-chart";
import type { AccountSummary, AccountTrendsResponse } from "@/features/accounts/schemas";
import { quotaBarColor, quotaBarTrack } from "@/utils/account-status";
import {
  formatCompactNumber,
  formatCurrency,
  formatPercentNullable,
  formatQuotaResetLabel,
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

function formatAdditionalLimitName(limitName: string): string {
  const map: Record<string, string> = { codex_other: "Codex Spark" };
  return map[limitName] ?? limitName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatResetCountdown(resetAt: number | null): string | null {
  if (resetAt === null) return null;
  const diffMs = resetAt * 1000 - Date.now();
  if (diffMs <= 0) return "Resetting...";
  if (diffMs >= 3600000) return `Resets in ${Math.floor(diffMs / 3600000)}h ${Math.floor((diffMs % 3600000) / 60000)}m`;
  return `Resets in ${Math.floor(diffMs / 60000)}m`;
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
        {!weeklyOnly && <QuotaRow label="Primary" percent={primary} resetAt={account.resetAtPrimary} />}
        <QuotaRow label="Secondary" percent={secondary} resetAt={account.resetAtSecondary} />
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
            <div key={quota.limitName} className="rounded-md border bg-background/60 px-3 py-2 space-y-2">
              <p className="text-xs font-medium">{formatAdditionalLimitName(quota.limitName)}</p>
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
                Primary
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-full bg-chart-2" />
                Secondary
              </span>
            </div>
          </div>
          <AccountTrendChart primary={trends.primary} secondary={trends.secondary} />
        </div>
      )}
    </div>
  );
}
