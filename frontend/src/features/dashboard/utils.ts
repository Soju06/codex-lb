import { Activity, AlertTriangle, Coins, DollarSign, Flame } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { buildDonutPalette } from "@/utils/colors";
import { buildDuplicateAccountIdSet, formatCompactAccountId } from "@/utils/account-identifiers";
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
  Depletion,
  RequestLog,
  TrendPoint,
  UsageWindow,
} from "@/features/dashboard/schemas";

const PLUS_DEFAULT_CAPACITY = {
  primary: 225,
  secondary: 7560,
} as const;

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

type DashboardViewOptions = {
  isDark?: boolean;
  showAccountBurnrate?: boolean;
};

function resolveDashboardViewOptions(optionsOrIsDark: DashboardViewOptions | boolean): Required<DashboardViewOptions> {
  if (typeof optionsOrIsDark === "boolean") {
    return {
      isDark: optionsOrIsDark,
      showAccountBurnrate: true,
    };
  }
  return {
    isDark: optionsOrIsDark.isDark ?? false,
    showAccountBurnrate: optionsOrIsDark.showAccountBurnrate ?? true,
  };
}

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

export function avgPerHour(cost7d: number, hours = 24 * 7): number {
  if (!Number.isFinite(cost7d) || cost7d <= 0 || hours <= 0) {
    return 0;
  }
  return cost7d / hours;
}

function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function clampPercent(value: number): number {
  return Math.min(100, Math.max(0, value));
}

function windowUsedAccountEquivalents(
  overview: DashboardOverview,
  windowKey: "primary" | "secondary",
): number | null {
  let usedEquivalent = 0;
  let includedAccounts = 0;

  for (const account of overview.accounts) {
    const windowMinutes = windowKey === "primary" ? account.windowMinutesPrimary : account.windowMinutesSecondary;
    const remainingPercent =
      windowKey === "primary" ? account.usage?.primaryRemainingPercent : account.usage?.secondaryRemainingPercent;

    if (windowMinutes == null || !isFiniteNumber(remainingPercent)) {
      continue;
    }

    usedEquivalent += (100 - clampPercent(remainingPercent)) / 100;
    includedAccounts += 1;
  }

  return includedAccounts > 0 ? usedEquivalent : null;
}

function windowProjectedAccountEquivalents(
  overview: DashboardOverview,
  windowKey: "primary" | "secondary",
): number | null {
  let projectedEquivalent = 0;
  let includedAccounts = 0;
  const nowMs = Date.now();

  for (const account of overview.accounts) {
    const windowMinutes = windowKey === "primary" ? account.windowMinutesPrimary : account.windowMinutesSecondary;
    const remainingPercent =
      windowKey === "primary" ? account.usage?.primaryRemainingPercent : account.usage?.secondaryRemainingPercent;
    const resetAt = windowKey === "primary" ? account.resetAtPrimary : account.resetAtSecondary;

    if (windowMinutes == null || !isFiniteNumber(remainingPercent) || windowMinutes <= 0) {
      continue;
    }

    const usedEquivalent = (100 - clampPercent(remainingPercent)) / 100;
    let projected = usedEquivalent;

    if (resetAt) {
      const resetAtMs = Date.parse(resetAt);
      if (Number.isFinite(resetAtMs)) {
        const windowSeconds = windowMinutes * 60;
        const secondsUntilReset = Math.max(0, (resetAtMs - nowMs) / 1000);
        const elapsedSeconds = Math.max(0, windowSeconds - secondsUntilReset);
        if (elapsedSeconds > 0) {
          projected = usedEquivalent * (windowSeconds / elapsedSeconds);
        }
      }
    }

    projectedEquivalent += projected;
    includedAccounts += 1;
  }

  return includedAccounts > 0 ? projectedEquivalent : null;
}

function windowIncludedAccountCount(
  overview: DashboardOverview,
  windowKey: "primary" | "secondary",
): number {
  let includedAccounts = 0;

  for (const account of overview.accounts) {
    const windowMinutes = windowKey === "primary" ? account.windowMinutesPrimary : account.windowMinutesSecondary;
    const remainingPercent =
      windowKey === "primary" ? account.usage?.primaryRemainingPercent : account.usage?.secondaryRemainingPercent;

    if (windowMinutes == null || !isFiniteNumber(remainingPercent)) {
      continue;
    }

    includedAccounts += 1;
  }

  return includedAccounts;
}

function plusAccountsBurnEquivalent(
  overview: DashboardOverview,
  windowKey: "primary" | "secondary",
): number | null {
  const summaryWindow = windowKey === "primary" ? overview.summary.primaryWindow : overview.summary.secondaryWindow;
  const depletion = windowKey === "primary" ? overview.depletionPrimary : overview.depletionSecondary;
  const fallbackProjectedEquivalent = windowProjectedAccountEquivalents(overview, windowKey);
  const fallbackUsedEquivalent = windowUsedAccountEquivalents(overview, windowKey);

  if (!summaryWindow) {
    return fallbackProjectedEquivalent ?? fallbackUsedEquivalent;
  }

  const remainingCredits = summaryWindow.remainingCredits;
  const burnRate = depletion?.burnRate;
  let burnEquivalent: number | null = null;

  if (isFiniteNumber(remainingCredits) && remainingCredits >= 0 && isFiniteNumber(burnRate) && burnRate > 0) {
    const plusCapacity = PLUS_DEFAULT_CAPACITY[windowKey];
    const equivalent = (remainingCredits * burnRate) / plusCapacity;
    if (isFiniteNumber(equivalent)) {
      burnEquivalent = Math.max(0, equivalent);
    }
  }

  if (burnEquivalent !== null) {
    const maxEquivalent = windowIncludedAccountCount(overview, windowKey);
    if (maxEquivalent > 0) {
      burnEquivalent = Math.min(burnEquivalent, maxEquivalent);
    }
  }

  if (windowKey === "secondary") {
    if (isFiniteNumber(fallbackProjectedEquivalent)) {
      return burnEquivalent === null ? fallbackProjectedEquivalent : Math.max(burnEquivalent, fallbackProjectedEquivalent);
    }
    if (isFiniteNumber(fallbackUsedEquivalent)) {
      return burnEquivalent === null ? fallbackUsedEquivalent : Math.max(burnEquivalent, fallbackUsedEquivalent);
    }
  }

  return burnEquivalent ?? fallbackProjectedEquivalent ?? fallbackUsedEquivalent;
}

function formatBurnEquivalent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) {
    return "--";
  }
  return value.toFixed(1);
}

function buildBurnTrend(points: TrendPoint[], currentValue: number | null): { value: number }[] {
  if (currentValue === null || !Number.isFinite(currentValue) || currentValue <= 0 || points.length === 0) {
    return [];
  }

  const lastPoint = points[points.length - 1]?.v ?? 0;
  if (!Number.isFinite(lastPoint) || lastPoint <= 0) {
    return points.map(() => ({ value: currentValue }));
  }

  const scale = currentValue / lastPoint;
  return points.map((point) => ({ value: Math.max(0, point.v * scale) }));
}

const TREND_COLORS = ["#3b82f6", "#8b5cf6", "#10b981", "#ef4444", "#f59e0b"];

function trendPointsToValues(points: TrendPoint[]): { value: number }[] {
  return points.map((p) => ({ value: p.v }));
}

export function buildDashboardView(
  overview: DashboardOverview,
  requestLogs: RequestLog[],
  optionsOrIsDark: DashboardViewOptions | boolean = false,
): DashboardView {
  const { isDark, showAccountBurnrate } = resolveDashboardViewOptions(optionsOrIsDark);
  const primaryWindow = overview.windows.primary;
  const secondaryWindow = overview.windows.secondary;
  const metrics = overview.summary.metrics;
  const cost = overview.summary.cost.totalUsd7d;
  const secondaryLabel = formatWindowLabel("secondary", secondaryWindow?.windowMinutes ?? null);
  const primaryBurnLabel = formatWindowLabel("primary", overview.summary.primaryWindow.windowMinutes ?? null);
  const secondaryBurnLabel = formatWindowLabel("secondary", overview.summary.secondaryWindow?.windowMinutes ?? null);
  const trends = overview.trends;

  const primaryBurnEquivalent = plusAccountsBurnEquivalent(overview, "primary");
  const secondaryBurnEquivalent = plusAccountsBurnEquivalent(overview, "secondary");
  const combinedBurnEquivalent =
    (primaryBurnEquivalent ?? 0) + (secondaryBurnEquivalent ?? 0) > 0
      ? (primaryBurnEquivalent ?? 0) + (secondaryBurnEquivalent ?? 0)
      : null;

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
  ];

  if (showAccountBurnrate) {
    stats.push({
      label: `Account burn rate (${primaryBurnLabel}/${secondaryBurnLabel})`,
      value: `${formatBurnEquivalent(primaryBurnEquivalent)} / ${formatBurnEquivalent(secondaryBurnEquivalent)}`,
      meta: `Primary ${formatBurnEquivalent(primaryBurnEquivalent)} acc/${primaryBurnLabel} · Secondary ${formatBurnEquivalent(secondaryBurnEquivalent)} acc/${secondaryBurnLabel}`,
      icon: Flame,
      trend: buildBurnTrend(trends.tokens, combinedBurnEquivalent),
      trendColor: TREND_COLORS[3],
    });
  }

  stats.push({
    label: "Error rate",
    value: formatRate(metrics?.errorRate7d ?? null),
    meta: metrics?.topError
      ? `Top: ${metrics.topError}`
      : `~${formatCompactNumber(Math.round((metrics?.errorRate7d ?? 0) * (metrics?.requests7d ?? 0)))} errors in 7d`,
    icon: AlertTriangle,
    trend: trendPointsToValues(trends.errorRate),
    trendColor: TREND_COLORS[4],
  });

  return {
    stats,
    primaryUsageItems: buildRemainingItems(overview.accounts, primaryWindow, "primary", isDark),
    secondaryUsageItems: buildRemainingItems(overview.accounts, secondaryWindow, "secondary", isDark),
    requestLogs,
    safeLinePrimary: buildDepletionView(overview.depletionPrimary),
    safeLineSecondary: buildDepletionView(overview.depletionSecondary),
  };
}
