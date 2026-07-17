import { lazy, Suspense } from "react";
import { Clock, Flame, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { AccountTrendChartProps } from "@/features/accounts/components/account-trend-chart";
import type {
  AccountSummary,
  AccountTrendsResponse,
  AccountUsageResetCredits,
} from "@/features/accounts/schemas";
import { quotaBarColor, quotaBarTrack } from "@/utils/account-status";
import {
  formatCompactNumber,
  formatCurrency,
  formatLocalDateTimeSeconds,
  formatPercentNullable,
  formatQuotaResetLabel,
  formatResetRelative,
  formatSingleUnitRemaining,
  formatWindowLabel,
} from "@/utils/formatters";

const AccountTrendChart = lazy(() =>
  import("@/features/accounts/components/account-trend-chart").then((module) => ({
    default: (props: AccountTrendChartProps) => <module.AccountTrendChart {...props} />,
  })),
);

export type AccountUsagePanelProps = {
  account: AccountSummary;
  trends?: AccountTrendsResponse | null;
  resetCredits?: AccountUsageResetCredits | null;
  resetCreditsLoading?: boolean;
  resetCreditsUnavailable?: boolean;
  resetDisabled?: boolean;
  onReset?: (accountId: string) => void;
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
  const { t } = useTranslation();
  const clamped = percent === null ? 0 : Math.max(0, Math.min(100, percent));
  const hasPercent = percent !== null;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium">{t("accounts.usage.remainingLabel", { label })}</span>
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
        <span>{t("accounts.usage.resetAt", { label: formatQuotaResetLabel(resetAt ?? null) })}</span>
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
  if (diffMs <= 0) return "resetting";
  return formatResetRelative(diffMs);
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
  const { t } = useTranslation();
  const clamped = Math.max(0, Math.min(100, usedPercent));
  const countdown = formatResetCountdown(resetAt);
  const countdownLabel = countdown === "resetting"
    ? t("formatters.resetting")
    : countdown
      ? t("accounts.usage.resetsAt", { label: countdown })
      : null;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums font-medium">{t("accounts.usage.percentUsed", { percent: Math.round(usedPercent) })}</span>
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
      {countdownLabel ? <p className="text-[11px] text-muted-foreground">{countdownLabel}</p> : null}
    </div>
  );
}

const ADDITIONAL_ROUTING_POLICY_LABELS: Record<string, string> = {
  burn_first: "Burn first",
  normal: "Normal",
  preserve: "Preserve",
};

function ResetCreditsRow({
  accountId,
  resetCredits,
  loading,
  unavailable,
  nearestExpiresAt,
  resetDisabled,
  onReset,
}: {
  accountId: string;
  resetCredits?: AccountUsageResetCredits | null;
  loading?: boolean;
  unavailable?: boolean;
  nearestExpiresAt?: string | null;
  resetDisabled?: boolean;
  onReset?: (accountId: string) => void;
}) {
  const { t } = useTranslation();
  if (resetCredits == null && !loading && !unavailable) {
    return null;
  }

  const availableCount = resetCredits?.availableCount ?? 0;
  const valueLabel =
    loading && resetCredits == null
      ? t("accounts.usage.resetCredits.checking")
      : unavailable && resetCredits == null
        ? t("common.states.unavailable")
        : t("accounts.usage.resetCredits.available", { count: availableCount });
  const expiryCountdown = nearestExpiresAt ? formatSingleUnitRemaining(nearestExpiresAt) : null;

  return (
    <div className="flex items-center justify-between gap-3 rounded-md border bg-background/60 px-3 py-2 text-xs">
      <span className="flex min-w-0 items-center gap-2 text-muted-foreground">
        <RotateCcw className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="truncate font-medium">{t("accounts.usage.resetCredits.title")}</span>
      </span>
      <span className="flex min-w-0 flex-1 items-center justify-end gap-2">
        {nearestExpiresAt && expiryCountdown ? (
          <span className="min-w-0 max-w-[min(18rem,42vw)] truncate text-[11px] text-muted-foreground">
            {t("accounts.usage.resetCredits.expires", { time: formatLocalDateTimeSeconds(nearestExpiresAt) })}{" "}
            <span className="tabular-nums">({expiryCountdown.label})</span>
          </span>
        ) : null}
        <span className="shrink-0 tabular-nums font-semibold">{valueLabel}</span>
        {onReset ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-6 shrink-0 gap-1 px-1.5 text-[11px]"
            aria-label={t("accounts.usageResetDialog.title")}
            onClick={() => onReset(accountId)}
            disabled={resetDisabled || availableCount <= 0}
          >
            <RotateCcw className="h-3 w-3" aria-hidden="true" />
            {t("common.actions.reset")}
          </Button>
        ) : null}
      </span>
    </div>
  );
}

export function AccountUsagePanel({
  account,
  trends,
  resetCredits,
  resetCreditsLoading,
  resetCreditsUnavailable,
  resetDisabled = false,
  onReset,
}: AccountUsagePanelProps) {
  const { t } = useTranslation();
  const primary = account.usage?.primaryRemainingPercent ?? null;
  const secondary = account.usage?.secondaryRemainingPercent ?? null;
  const monthly = account.usage?.monthlyRemainingPercent ?? null;
  const requestUsage = account.requestUsage ?? null;
  const hasRequestUsage = (requestUsage?.requestCount ?? 0) > 0;
  const weeklyOnly = account.windowMinutesPrimary == null && account.windowMinutesSecondary != null;
  const primaryTrendPoints = trends?.primary ?? [];
  const secondaryTrendPoints = trends?.secondary ?? [];
  const secondaryScheduledTrendPoints = trends?.secondaryScheduled ?? [];
  const monthlyOnly =
    account.windowMinutesMonthly != null &&
    account.windowMinutesPrimary == null &&
    account.windowMinutesSecondary == null;
  const hasTrends =
    primaryTrendPoints.length > 0 || secondaryTrendPoints.length > 0 || secondaryScheduledTrendPoints.length > 0;

  return (
    <div className="min-w-0 space-y-4 rounded-lg border bg-muted/30 p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t("accounts.usage.title")}</h3>
      <div className={cn("grid gap-4", weeklyOnly || monthlyOnly ? "grid-cols-1" : "grid-cols-1 sm:grid-cols-2")}>
        {monthlyOnly ? (
          <QuotaRow label={t("common.quota.monthly")} percent={monthly} resetAt={account.resetAtMonthly} />
        ) : (
          <>
            {!weeklyOnly && <QuotaRow label="5h" percent={primary} resetAt={account.resetAtPrimary} />}
            <QuotaRow label={t("common.quota.weekly")} percent={secondary} resetAt={account.resetAtSecondary} />
          </>
        )}
      </div>
      <ResetCreditsRow
        accountId={account.accountId}
        resetCredits={resetCredits}
        loading={resetCreditsLoading}
        unavailable={resetCreditsUnavailable}
        nearestExpiresAt={account.resetCreditNearestExpiresAt ?? null}
        resetDisabled={resetDisabled}
        onReset={onReset}
      />
      <div className="rounded-md border bg-background/60 px-3 py-2">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{t("accounts.usage.requestLogsTotal")}</p>
        {hasRequestUsage ? (
          <p className="mt-1 break-words text-xs tabular-nums text-muted-foreground">
            {t("accounts.usage.requestUsageMeta", {
              tokens: formatCompactNumber(requestUsage?.totalTokens),
              cached: formatCompactNumber(requestUsage?.cachedInputTokens),
              requests: formatCompactNumber(requestUsage?.requestCount),
              cost: formatCurrency(requestUsage?.totalCostUsd),
            })}
          </p>
        ) : (
          <p className="mt-1 text-xs text-muted-foreground">{t("accounts.usage.noRequestUsage")}</p>
        )}
      </div>
      {account.additionalQuotas.length > 0 ? (
        <div className="space-y-3">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t("accounts.usage.additionalQuotas")}
          </p>
          {account.additionalQuotas.map((quota) => (
            <div key={quota.quotaKey ?? quota.limitName} className="rounded-md border bg-background/60 px-3 py-2 space-y-2">
              <p className="text-xs font-medium">
                <span>{quota.displayLabel ?? formatAdditionalLimitName(quota.limitName, quota.quotaKey)}</span>
                {quota.routingPolicy != null && quota.routingPolicy !== "inherit" ? (
                  <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-orange-200 bg-orange-50 px-1.5 py-0.5 text-[10px] font-medium text-orange-700 dark:border-orange-900/60 dark:bg-orange-950/40 dark:text-orange-300">
                    <Flame className="h-3 w-3" aria-hidden="true" />
                    {t(`common.routingPolicies.${quota.routingPolicy === "burn_first" ? "burnFirst" : quota.routingPolicy}`, {
                      defaultValue: ADDITIONAL_ROUTING_POLICY_LABELS[quota.routingPolicy] ?? quota.routingPolicy,
                    })}
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
          <div className="mb-2 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t("accounts.usage.trendTitle")}</h4>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-full bg-chart-1" />
                5h
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-full bg-chart-2" />
                {monthlyOnly ? t("common.quota.monthly") : t("common.quota.weekly")}
              </span>
              {secondaryScheduledTrendPoints.length > 0 ? (
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-0 w-4 border-t border-dashed border-chart-2" />
                  {monthlyOnly ? t("accounts.usage.monthlyPlan") : t("accounts.usage.weeklyPlan")}
                </span>
              ) : null}
            </div>
          </div>
          <Suspense fallback={<div className="h-[220px]" />}>
            <AccountTrendChart
              primary={primaryTrendPoints}
              secondary={secondaryTrendPoints}
              secondaryScheduled={secondaryScheduledTrendPoints}
            />
          </Suspense>
        </div>
      )}
    </div>
  );
}
