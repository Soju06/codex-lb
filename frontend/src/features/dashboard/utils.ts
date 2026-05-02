import { Activity, AlertTriangle, Coins, DollarSign, type LucideIcon } from "lucide-react";

import type {
  AccountSummary,
  DashboardOverview,
  Depletion,
  RequestLog,
  TrendPoint,
  UsageWindow,
} from "@/features/dashboard/schemas";
import { buildDuplicateAccountIdSet, formatCompactAccountId } from "@/utils/account-identifiers";
import { buildDonutPalette } from "@/utils/colors";
import {
  formatCachedTokensMeta,
  formatCompactNumber,
  formatCurrency,
  formatRate,
  formatWindowMinutes,
} from "@/utils/formatters";

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

export type WeeklyCreditPaceStatus = "behind" | "on_track" | "ahead" | "danger";

export type WeeklyCreditPace = {
  totalFullCredits: number;
  totalActualRemainingCredits: number;
  totalExpectedRemainingCredits: number;
  actualUsedPercent: number;
  scheduledUsedPercent: number;
  deltaPercent: number;
  overPlanCredits: number;
  pauseForBreakEvenHours: number | null;
  paceMultiplier: number | null;
  throttleToPercent: number | null;
  reduceByPercent: number | null;
  proAccountEquivalentToCoverOverPlan: number | null;
  proAccountsToCoverOverPlan: number | null;
  projectedDepletionHours: number | null;
  projectedMinimumRemainingCredits: number | null;
  status: WeeklyCreditPaceStatus;
  accountCount: number;
};

export type DashboardView = {
  stats: DashboardStat[];
  primaryUsageItems: RemainingItem[];
  secondaryUsageItems: RemainingItem[];
  /** Sum of visible primary remaining items shown in the donut center label. */
  primaryTotal: number;
  /** Sum of visible secondary remaining items shown in the donut center label. */
  secondaryTotal: number;
  requestLogs: RequestLog[];
  safeLinePrimary: SafeLineView | null;
  safeLineSecondary: SafeLineView | null;
  weeklyCreditPace: WeeklyCreditPace | null;
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
    if (secondaryItem.remainingPercent == null) return item;
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
const PRO_WEEKLY_CAPACITY_CREDITS = 50_400;

function trendPointsToValues(points: TrendPoint[]): { value: number }[] {
  return points.map((p) => ({ value: p.v }));
}

/** Sum the `value` fields of remaining items (clamped to >= 0). */
export function sumRemaining(items: RemainingItem[]): number {
  return items.reduce((sum, item) => sum + Math.max(0, item.value), 0);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function isPositiveFinite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function isNonNegativeFinite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function weeklyCreditPaceStatus(deltaPercent: number, projectedShortfallCredits: number): WeeklyCreditPaceStatus {
  if (projectedShortfallCredits > 0) return "danger";
  if (deltaPercent < -5) return "behind";
  return "on_track";
}

type WeeklyPoolAccount = {
  fullCredits: number;
  remainingCredits: number;
  resetAtMs: number;
  windowMs: number;
};

type WeeklyPoolProjection = {
  burnRateCreditsPerMs: number;
  projectedShortfallCredits: number;
  projectedDepletionHours: number | null;
  projectedMinimumRemainingCredits: number;
  firstReplenishmentWaitMs: number | null;
};

type WeeklyResetEvent = {
  resetAtMs: number;
  topUpCredits: number;
  recurringTopUpCredits: number;
  windowMs: number;
};

function buildWeeklyPoolProjection(accounts: WeeklyPoolAccount[], nowMs: number): WeeklyPoolProjection | null {
  const totalRemainingCredits = accounts.reduce((sum, account) => sum + account.remainingCredits, 0);
  const totalUsedCredits = accounts.reduce(
    (sum, account) => sum + Math.max(0, account.fullCredits - account.remainingCredits),
    0,
  );
  const oldestWindowStartMs = Math.min(...accounts.map((account) => account.resetAtMs - account.windowMs));
  const elapsedMs = nowMs - oldestWindowStartMs;
  if (
    totalUsedCredits <= 0 ||
    !Number.isFinite(elapsedMs) ||
    elapsedMs <= 0
  ) {
    return null;
  }

  const burnRateCreditsPerMs = totalUsedCredits / elapsedMs;
  const firstResetAtMs = Math.min(...accounts.map((account) => account.resetAtMs));
  if (totalRemainingCredits <= 0) {
    const firstReplenishmentWaitMs = Number.isFinite(firstResetAtMs) ? Math.max(0, firstResetAtMs - nowMs) : 0;
    return {
      burnRateCreditsPerMs,
      projectedShortfallCredits: burnRateCreditsPerMs * firstReplenishmentWaitMs,
      projectedDepletionHours: 0,
      projectedMinimumRemainingCredits: 0,
      firstReplenishmentWaitMs,
    };
  }

  const resetEvents: WeeklyResetEvent[] = accounts
    .filter((account) => account.resetAtMs > nowMs)
    .map((account) => ({
      resetAtMs: account.resetAtMs,
      topUpCredits: Math.max(0, account.fullCredits - account.remainingCredits),
      recurringTopUpCredits: account.fullCredits,
      windowMs: account.windowMs,
    }));
  if (resetEvents.length === 0) {
    return {
      burnRateCreditsPerMs,
      projectedShortfallCredits: 0,
      projectedDepletionHours: null,
      projectedMinimumRemainingCredits: totalRemainingCredits,
      firstReplenishmentWaitMs: null,
    };
  }

  let cursorMs = nowMs;
  let balanceCredits = totalRemainingCredits;
  let minimumRemainingCredits = totalRemainingCredits;
  const longestWindowMs = Math.max(...accounts.map((account) => account.windowMs));
  const horizonMs = nowMs + longestWindowMs * 2;

  while (cursorMs < horizonMs) {
    resetEvents.sort((a, b) => a.resetAtMs - b.resetAtMs);
    const event = resetEvents[0];
    const nextEventAtMs = Math.min(event.resetAtMs, horizonMs);
    const intervalMs = nextEventAtMs - cursorMs;
    const intervalBurnCredits = burnRateCreditsPerMs * intervalMs;
    if (intervalBurnCredits >= balanceCredits) {
      const projectedShortfallCredits = intervalBurnCredits - balanceCredits;
      return {
        burnRateCreditsPerMs,
        projectedShortfallCredits,
        projectedDepletionHours: balanceCredits / burnRateCreditsPerMs / 3_600_000,
        projectedMinimumRemainingCredits: 0,
        firstReplenishmentWaitMs: nextEventAtMs - cursorMs,
      };
    }

    balanceCredits -= intervalBurnCredits;
    minimumRemainingCredits = Math.min(minimumRemainingCredits, balanceCredits);
    cursorMs = nextEventAtMs;
    if (cursorMs >= horizonMs) {
      break;
    }

    balanceCredits += event.topUpCredits;
    event.topUpCredits = event.recurringTopUpCredits;
    event.resetAtMs += event.windowMs;
  }

  return {
    burnRateCreditsPerMs,
    projectedShortfallCredits: 0,
    projectedDepletionHours: null,
    projectedMinimumRemainingCredits: minimumRemainingCredits,
    firstReplenishmentWaitMs: null,
  };
}

export function buildWeeklyCreditPace(
  accounts: AccountSummary[],
  now: Date = new Date(),
): WeeklyCreditPace | null {
  const nowMs = now.getTime();
  if (!Number.isFinite(nowMs)) {
    return null;
  }

  let totalFullCredits = 0;
  let totalActualRemainingCredits = 0;
  let totalExpectedRemainingCredits = 0;
  let accountCount = 0;
  const weeklyAccounts: WeeklyPoolAccount[] = [];

  for (const account of accounts) {
    const fullCredits = account.capacityCreditsSecondary;
    const remainingCredits = account.remainingCreditsSecondary;
    const resetAtMs = account.resetAtSecondary ? Date.parse(account.resetAtSecondary) : Number.NaN;
    const windowMinutes = account.windowMinutesSecondary;

    if (
      !isPositiveFinite(fullCredits) ||
      !isNonNegativeFinite(remainingCredits) ||
      !Number.isFinite(resetAtMs) ||
      !isPositiveFinite(windowMinutes)
    ) {
      continue;
    }

    const windowMs = windowMinutes * 60_000;
    const timeLeftMs = clamp(resetAtMs - nowMs, 0, windowMs);
    const expectedRemainingCredits = fullCredits * (timeLeftMs / windowMs);
    const actualRemainingCredits = clamp(remainingCredits, 0, fullCredits);

    totalFullCredits += fullCredits;
    totalActualRemainingCredits += actualRemainingCredits;
    totalExpectedRemainingCredits += expectedRemainingCredits;
    accountCount += 1;
    weeklyAccounts.push({
      fullCredits,
      remainingCredits: actualRemainingCredits,
      resetAtMs: Math.max(resetAtMs, nowMs),
      windowMs,
    });
  }

  if (accountCount === 0 || totalFullCredits <= 0) {
    return null;
  }

  const actualUsedPercent = (100 * (totalFullCredits - totalActualRemainingCredits)) / totalFullCredits;
  const scheduledUsedPercent = (100 * (totalFullCredits - totalExpectedRemainingCredits)) / totalFullCredits;
  const deltaPercent = actualUsedPercent - scheduledUsedPercent;
  const projection = buildWeeklyPoolProjection(weeklyAccounts, nowMs);
  const overPlanCredits = projection?.projectedShortfallCredits ?? 0;
  const pauseForBreakEvenHours =
    projection && overPlanCredits > 0 && projection.burnRateCreditsPerMs > 0
      ? overPlanCredits / projection.burnRateCreditsPerMs / 3_600_000
      : null;
  const paceMultiplier = overPlanCredits > 0 && scheduledUsedPercent > 0 ? actualUsedPercent / scheduledUsedPercent : null;
  const throttleToPercent =
    projection && overPlanCredits > 0 && projection.firstReplenishmentWaitMs && projection.burnRateCreditsPerMs > 0
      ? clamp(
          ((projection.firstReplenishmentWaitMs * projection.burnRateCreditsPerMs - overPlanCredits) /
            (projection.firstReplenishmentWaitMs * projection.burnRateCreditsPerMs)) *
            100,
          0,
          100,
        )
      : null;
  const reduceByPercent = throttleToPercent != null ? 100 - throttleToPercent : null;
  const proAccountEquivalentToCoverOverPlan =
    overPlanCredits > 0 ? overPlanCredits / PRO_WEEKLY_CAPACITY_CREDITS : null;
  const proAccountsToCoverOverPlan =
    overPlanCredits > 0 ? Math.ceil(overPlanCredits / PRO_WEEKLY_CAPACITY_CREDITS) : null;

  return {
    totalFullCredits,
    totalActualRemainingCredits,
    totalExpectedRemainingCredits,
    actualUsedPercent,
    scheduledUsedPercent,
    deltaPercent,
    overPlanCredits,
    pauseForBreakEvenHours,
    paceMultiplier,
    throttleToPercent,
    reduceByPercent,
    proAccountEquivalentToCoverOverPlan,
    proAccountsToCoverOverPlan,
    projectedDepletionHours: projection?.projectedDepletionHours ?? null,
    projectedMinimumRemainingCredits: projection?.projectedMinimumRemainingCredits ?? null,
    status: weeklyCreditPaceStatus(deltaPercent, overPlanCredits),
    accountCount,
  };
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
  const costAverage =
    timeframeHours <= 24
      ? `Avg/hr ${formatCurrency(avgPerUnit(cost, timeframeHours))}`
      : `Avg/day ${formatCurrency(avgPerUnit(cost, timeframeDays))}`;
  const costMeta =
    metrics?.cachedInputTokens && metrics.cachedInputTokens > 0
      ? `${costAverage} · API estimate, ${formatCompactNumber(metrics.cachedInputTokens)} cached`
      : `${costAverage} · API estimate`;
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
      label: `Est. API Cost (${timeframeLabel})`,
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
  const primaryUsageItems = secondaryWindow
    ? applySecondaryConstraint(rawPrimaryItems, secondaryUsageItems)
    : rawPrimaryItems;

  return {
    stats,
    primaryUsageItems,
    secondaryUsageItems,
    primaryTotal: sumRemaining(primaryUsageItems),
    secondaryTotal: sumRemaining(secondaryUsageItems),
    requestLogs,
    safeLinePrimary: buildDepletionView(overview.depletionPrimary),
    safeLineSecondary: buildDepletionView(overview.depletionSecondary),
    weeklyCreditPace: buildWeeklyCreditPace(overview.accounts),
  };
}
