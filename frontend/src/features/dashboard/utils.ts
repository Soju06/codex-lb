import { buildDonutGradient as buildDonutGradientBase, buildDonutPalette } from "@/utils/colors";
import {
  formatCompactNumber,
  formatCurrency,
  formatRate,
  formatWindowLabel,
} from "@/utils/formatters";

import type {
  AccountSummary,
  DashboardOverview,
  RequestLog,
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
): RemainingItem[] {
  const usageIndex = buildWindowIndex(window);
  const palette = buildDonutPalette(accounts.length);

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

export function buildDonutGradient(items: RemainingItem[], total: number): string {
  return buildDonutGradientBase(
    items.map((item) => ({ value: item.value, color: item.color })),
    total,
  );
}

export function avgPerHour(cost7d: number, hours = 24 * 7): number {
  if (!Number.isFinite(cost7d) || cost7d <= 0 || hours <= 0) {
    return 0;
  }
  return cost7d / hours;
}

export function buildDashboardView(
  overview: DashboardOverview,
  requestLogs: RequestLog[],
): DashboardView {
  const primaryWindow = overview.windows.primary;
  const secondaryWindow = overview.windows.secondary;
  const metrics = overview.summary.metrics;
  const cost = overview.summary.cost.totalUsd7d;
  const secondaryLabel = formatWindowLabel("secondary", secondaryWindow?.windowMinutes ?? null);

  const stats: DashboardStat[] = [
    {
      label: "Requests (7d)",
      value: formatCompactNumber(metrics?.requests7d ?? 0),
    },
    {
      label: `Tokens (${secondaryLabel})`,
      value: formatCompactNumber(metrics?.tokensSecondaryWindow ?? 0),
    },
    {
      label: "Cost (7d)",
      value: formatCurrency(cost),
      meta: `Avg/hr ${formatCurrency(avgPerHour(cost))}`,
    },
    {
      label: "Error rate",
      value: formatRate(metrics?.errorRate7d ?? null),
      meta: metrics?.topError ? `Top: ${metrics.topError}` : undefined,
    },
  ];

  return {
    stats,
    primaryUsageItems: buildRemainingItems(overview.accounts, primaryWindow),
    secondaryUsageItems: buildRemainingItems(overview.accounts, secondaryWindow),
    requestLogs,
  };
}
