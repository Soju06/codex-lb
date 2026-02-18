import { Activity, AlertTriangle, Coins, DollarSign } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { buildDonutPalette } from "@/utils/colors";
import {
  formatCachedTokensMeta,
  formatCompactNumber,
  formatCurrency,
  formatRate,
  formatWindowLabel,
} from "@/utils/formatters";

import type {
  AccountSummary,
  DashboardOverview,
  RequestLog,
  TrendPoint,
  UsageWindow,
} from "@/features/dashboard/schemas";

export type RemainingItem = {
  accountId: string;
  label: string;
  value: number;
  remainingPercent: number;
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

export type DashboardView = {
  stats: DashboardStat[];
  primaryUsageItems: RemainingItem[];
  secondaryUsageItems: RemainingItem[];
  requestLogs: RequestLog[];
};

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

export function buildRemainingItems(
  accounts: AccountSummary[],
  window: UsageWindow | null,
  isDark = false,
): RemainingItem[] {
  const usageIndex = buildWindowIndex(window);
  const palette = buildDonutPalette(accounts.length, isDark);

  return accounts.map((account, index) => {
    const fallbackPercent = account.usage?.primaryRemainingPercent ?? 0;
    const remaining = usageIndex.get(account.accountId) ?? 0;
    return {
      accountId: account.accountId,
      label: account.displayName || account.email || account.accountId,
      value: remaining,
      remainingPercent: fallbackPercent,
      color: palette[index % palette.length],
    };
  });
}

export function avgPerHour(cost7d: number, hours = 24 * 7): number {
  if (!Number.isFinite(cost7d) || cost7d <= 0 || hours <= 0) {
    return 0;
  }
  return cost7d / hours;
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
  const cost = overview.summary.cost.totalUsd7d;
  const secondaryLabel = formatWindowLabel("secondary", secondaryWindow?.windowMinutes ?? null);
  const trends = overview.trends;

  const stats: DashboardStat[] = [
    {
      label: "Requests (7d)",
      value: formatCompactNumber(metrics?.requests7d ?? 0),
      meta: `Avg/day ${formatCompactNumber(Math.round((metrics?.requests7d ?? 0) / 7))}`,
      icon: Activity,
      trend: trendPointsToValues(trends.requests),
      trendColor: TREND_COLORS[0],
    },
    {
      label: `Tokens (${secondaryLabel})`,
      value: formatCompactNumber(metrics?.tokensSecondaryWindow ?? 0),
      meta: formatCachedTokensMeta(metrics?.tokensSecondaryWindow, metrics?.cachedTokensSecondaryWindow),
      icon: Coins,
      trend: trendPointsToValues(trends.tokens),
      trendColor: TREND_COLORS[1],
    },
    {
      label: "Cost (7d)",
      value: formatCurrency(cost),
      meta: `Avg/hr ${formatCurrency(avgPerHour(cost))}`,
      icon: DollarSign,
      trend: trendPointsToValues(trends.cost),
      trendColor: TREND_COLORS[2],
    },
    {
      label: "Error rate",
      value: formatRate(metrics?.errorRate7d ?? null),
      meta: metrics?.topError
        ? `Top: ${metrics.topError}`
        : `~${formatCompactNumber(Math.round((metrics?.errorRate7d ?? 0) * (metrics?.requests7d ?? 0)))} errors in 7d`,
      icon: AlertTriangle,
      trend: trendPointsToValues(trends.errorRate),
      trendColor: TREND_COLORS[3],
    },
  ];

  return {
    stats,
    primaryUsageItems: buildRemainingItems(overview.accounts, primaryWindow, isDark),
    secondaryUsageItems: buildRemainingItems(overview.accounts, secondaryWindow, isDark),
    requestLogs,
  };
}
