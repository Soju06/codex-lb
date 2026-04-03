import { Activity, AlertTriangle, Coins, DollarSign } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { buildDonutPalette } from "@/utils/colors";
import { buildDuplicateAccountIdSet, formatCompactAccountId } from "@/utils/account-identifiers";
import {
  formatCachedTokensMeta,
  formatCompactNumber,
  formatCurrency,
  formatRate,
  formatWindowMinutes,
} from "@/utils/formatters";

import type {
  AccountSummary,
  DashboardOverview,
  Depletion,
  RequestLog,
  TrendPoint,
  UsageWindow,
} from "@/features/dashboard/schemas";

export type RemainingItem = {
  accountId: string;
  label: string;
  /** Suffix appended after the label (e.g. compact account ID for duplicates). Not blurred. */
  labelSuffix: string;
  /** True when the displayed label is the account email (should be blurred in privacy mode). */
  isEmail: boolean;
  value: number;
  remainingPercent: number | null;
  color: string;
};

export type DashboardStat = {
  label: string;
  value: string;
  meta?: string;
  icon: LucideIcon;
  trend: { value: number }[];
  trendColor: string;
};

export interface SafeLineView {
  safePercent: number;
  riskLevel: "safe" | "warning" | "danger" | "critical";
}

export type DashboardView = {
  stats: DashboardStat[];
  primaryUsageItems: RemainingItem[];
  secondaryUsageItems: RemainingItem[];
  requestLogs: RequestLog[];
  safeLinePrimary: SafeLineView | null;
  safeLineSecondary: SafeLineView | null;
};

export function buildDepletionView(depletion: Depletion | null | undefined): SafeLineView | null {
  if (!depletion || depletion.riskLevel === "safe") return null;
  return { safePercent: depletion.safeUsagePercent, riskLevel: depletion.riskLevel };
}

function buildWindowIndex(window: UsageWindow | null): Map<string, number> {
  const index = new Map<string, number>();
  if (!window) {
    return index;
  }
  for (const entry of window.accounts) {
    index.set(entry.accountId, entry.remainingCredits);
  }
  return index;
}

function isWeeklyOnlyAccount(account: AccountSummary): boolean {
  return account.windowMinutesPrimary == null && account.windowMinutesSecondary != null;
}

function accountRemainingPercent(account: AccountSummary, windowKey: "primary" | "secondary"): number | null {
  if (windowKey === "secondary") {
    return account.usage?.secondaryRemainingPercent ?? null;
  }
  return account.usage?.primaryRemainingPercent ?? null;
}

/**
 * Cap primary (5h) remaining by secondary (7d) absolute credits.
 *
 * The 7d window is a hard quota gate — when its remaining credits are lower
 * than the 5h remaining credits, the account can only use up to the 7d amount
 * regardless of 5h headroom.  Comparing absolute credits (not percentages) is
 * essential because the two windows have vastly different capacities
 * (e.g. 225 vs 7 560 for Plus plans).
 */
export function applySecondaryConstraint(
  primaryItems: RemainingItem[],
  secondaryItems: RemainingItem[],
): RemainingItem[] {
  const secondaryByAccount = new Map<string, RemainingItem>();
  for (const item of secondaryItems) {
    secondaryByAccount.set(item.accountId, item);
  }

  return primaryItems.map((item) => {
    const secondaryItem = secondaryByAccount.get(item.accountId);
    if (!secondaryItem) return item;
    if (secondaryItem.value >= item.value) return item;

    const effectivePercent =
      item.remainingPercent != null && item.value > 0
        ? item.remainingPercent * (secondaryItem.value / item.value)
        : item.remainingPercent;

    return {
      ...item,
      value: Math.max(0, secondaryItem.value),
      remainingPercent: effectivePercent != null ? Math.max(0, effectivePercent) : null,
    };
  });
}

export function buildRemainingItems(
  accounts: AccountSummary[],
  window: UsageWindow | null,
  windowKey: "primary" | "secondary",
  isDark = false,
): RemainingItem[] {
  const usageIndex = buildWindowIndex(window);
  const palette = buildDonutPalette(accounts.length, isDark);
  const duplicateAccountIds = buildDuplicateAccountIdSet(accounts);

  return accounts
    .map((account, index) => {
      if (windowKey === "primary" && isWeeklyOnlyAccount(account)) {
        return null;
      }
      const remaining = usageIndex.get(account.accountId) ?? 0;
      const rawLabel = account.displayName || account.email || account.accountId;
      const labelIsEmail = !!account.email && rawLabel === account.email;
      const labelSuffix = duplicateAccountIds.has(account.accountId)
        ? ` (${formatCompactAccountId(account.accountId, 5, 4)})`
        : "";
      return {
        accountId: account.accountId,
        label: rawLabel,
        labelSuffix,
        isEmail: labelIsEmail,
        value: remaining,
        remainingPercent: accountRemainingPercent(account, windowKey),
        color: palette[index % palette.length],
      };
    })
    .filter((item): item is RemainingItem => item !== null);
}

function avgPerUnit(total: number, units: number): number {
  if (!Number.isFinite(total) || total <= 0 || units <= 0) {
    return 0;
  }
  return total / units;
}

const TREND_COLORS = ["#3b82f6", "#8b5cf6", "#10b981", "#f59e0b"];

function trendPointsToValues(points: TrendPoint[]): { value: number }[] {
  return points.map((p) => ({ value: p.v }));
}

export function buildDashboardView(
  overview: DashboardOverview,
  requestLogs: RequestLog[],
  isDark = false,
): DashboardView {
  const primaryWindow = overview.windows.primary;
  const secondaryWindow = overview.windows.secondary;
  const metrics = overview.summary.metrics;
  const cost = overview.summary.cost.totalUsd;
  const timeframeLabel = (() => {
    const formatted = formatWindowMinutes(overview.timeframe.windowMinutes);
    return formatted === "--" ? overview.timeframe.key : formatted;
  })();
  const timeframeHours = overview.timeframe.windowMinutes / 60;
  const timeframeDays = overview.timeframe.windowMinutes / 1440;
  const requestMeta =
    timeframeHours <= 24
      ? `Avg/hr ${formatCompactNumber(Math.round(avgPerUnit(metrics?.requests ?? 0, timeframeHours)))}`
      : `Avg/day ${formatCompactNumber(Math.round(avgPerUnit(metrics?.requests ?? 0, timeframeDays)))}`;
  const costMeta =
    timeframeHours <= 24
      ? `Avg/hr ${formatCurrency(avgPerUnit(cost, timeframeHours))}`
      : `Avg/day ${formatCurrency(avgPerUnit(cost, timeframeDays))}`;
  const trends = overview.trends;

  const stats: DashboardStat[] = [
    {
      label: `Requests (${timeframeLabel})`,
      value: formatCompactNumber(metrics?.requests ?? 0),
      meta: requestMeta,
      icon: Activity,
      trend: trendPointsToValues(trends.requests),
      trendColor: TREND_COLORS[0],
    },
    {
      label: `Tokens (${timeframeLabel})`,
      value: formatCompactNumber(metrics?.tokens ?? 0),
      meta: formatCachedTokensMeta(metrics?.tokens, metrics?.cachedInputTokens),
      icon: Coins,
      trend: trendPointsToValues(trends.tokens),
      trendColor: TREND_COLORS[1],
    },
    {
      label: `Cost (${timeframeLabel})`,
      value: formatCurrency(cost),
      meta: costMeta,
      icon: DollarSign,
      trend: trendPointsToValues(trends.cost),
      trendColor: TREND_COLORS[2],
    },
    {
      label: `Error rate (${timeframeLabel})`,
      value: formatRate(metrics?.errorRate ?? null),
      meta: metrics?.topError
        ? `Top: ${metrics.topError}`
        : `~${formatCompactNumber(metrics?.errorCount ?? Math.round((metrics?.errorRate ?? 0) * (metrics?.requests ?? 0)))} errors in ${timeframeLabel}`,
      icon: AlertTriangle,
      trend: trendPointsToValues(trends.errorRate),
      trendColor: TREND_COLORS[3],
    },
  ];

  const rawPrimaryItems = buildRemainingItems(overview.accounts, primaryWindow, "primary", isDark);
  const secondaryUsageItems = buildRemainingItems(overview.accounts, secondaryWindow, "secondary", isDark);

  return {
    stats,
    primaryUsageItems: secondaryWindow
      ? applySecondaryConstraint(rawPrimaryItems, secondaryUsageItems)
      : rawPrimaryItems,
    secondaryUsageItems,
    requestLogs,
    safeLinePrimary: buildDepletionView(overview.depletionPrimary),
    safeLineSecondary: buildDepletionView(overview.depletionSecondary),
  };
}
