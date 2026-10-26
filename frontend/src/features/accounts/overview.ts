import type { AccountSummary } from "@/features/accounts/schemas";
import { normalizeStatus, type DashboardAccountStatus } from "@/utils/account-status";

export const OVERVIEW_STATUS_ORDER = [
  "active",
  "paused",
  "limited",
  "exceeded",
  "reauth",
  "deactivated",
] as const satisfies readonly DashboardAccountStatus[];

export const QUOTA_BIN_COUNT = 10;
const LOW_REMAINING_PERCENT = 10;
const WINDOW_KEYS = ["primary", "secondary", "monthly"] as const;

export type QuotaWindowKey = (typeof WINDOW_KEYS)[number];

export type WindowOverview = {
  key: QuotaWindowKey;
  /** Accounts that report this window (a percentage or a window length); a monthly-only free account does not report 5-hour or weekly. */
  reporting: number;
  /** Accounts with a known remaining percentage for this window. */
  known: number;
  /** Average remaining percent per account over `known`; null when nothing is known. */
  avgRemaining: number | null;
  lowCount: number;
  /** Ten buckets of remaining percent: [0,10), …, [90,100]. */
  bins: number[];
  nextResetAt: string | null;
};

export type PlanOverview = {
  /** Raw `planType`; null for the all-plans row. */
  plan: string | null;
  total: number;
  statusCounts: Record<DashboardAccountStatus, number>;
  windows: WindowOverview[];
};

type WindowAccumulator = WindowOverview & { present: boolean; sum: number; nextResetMs: number };
type GroupAccumulator = Omit<PlanOverview, "windows"> & { windows: Record<QuotaWindowKey, WindowAccumulator> };

function emptyGroup(plan: string | null): GroupAccumulator {
  const window = (key: QuotaWindowKey): WindowAccumulator => ({
    key,
    reporting: 0,
    known: 0,
    avgRemaining: null,
    lowCount: 0,
    bins: Array<number>(QUOTA_BIN_COUNT).fill(0),
    nextResetAt: null,
    present: false,
    sum: 0,
    nextResetMs: Number.POSITIVE_INFINITY,
  });
  return {
    plan,
    total: 0,
    statusCounts: { active: 0, paused: 0, limited: 0, exceeded: 0, reauth: 0, deactivated: 0 },
    windows: { primary: window("primary"), secondary: window("secondary"), monthly: window("monthly") },
  };
}

function windowFields(account: AccountSummary, key: QuotaWindowKey) {
  switch (key) {
    case "primary":
      return { remaining: account.usage?.primaryRemainingPercent, minutes: account.windowMinutesPrimary, resetAt: account.resetAtPrimary };
    case "secondary":
      return { remaining: account.usage?.secondaryRemainingPercent, minutes: account.windowMinutesSecondary, resetAt: account.resetAtSecondary };
    case "monthly":
      return { remaining: account.usage?.monthlyRemainingPercent, minutes: account.windowMinutesMonthly, resetAt: account.resetAtMonthly };
  }
}

function addAccount(group: GroupAccumulator, account: AccountSummary, now: number) {
  group.total += 1;
  group.statusCounts[normalizeStatus(account.status)] += 1;
  for (const key of WINDOW_KEYS) {
    const { remaining, minutes, resetAt } = windowFields(account, key);
    const window = group.windows[key];
    if (remaining == null && minutes == null) {
      continue;
    }
    window.present = true;
    window.reporting += 1;
    if (remaining != null && Number.isFinite(remaining)) {
      const value = Math.min(100, Math.max(0, remaining));
      window.known += 1;
      window.sum += value;
      window.bins[Math.min(QUOTA_BIN_COUNT - 1, Math.floor(value / 10))] += 1;
      if (value < LOW_REMAINING_PERCENT) {
        window.lowCount += 1;
      }
    }
    const resetMs = resetAt ? Date.parse(resetAt) : Number.NaN;
    if (resetMs > now && resetMs < window.nextResetMs) {
      window.nextResetMs = resetMs;
      window.nextResetAt = resetAt ?? null;
    }
  }
}

function finish(group: GroupAccumulator): PlanOverview {
  return {
    plan: group.plan,
    total: group.total,
    statusCounts: group.statusCounts,
    windows: WINDOW_KEYS.map((key) => group.windows[key])
      .filter((window) => window.present)
      .map(({ key, reporting, known, sum, lowCount, bins, nextResetAt }) => ({
        key,
        reporting,
        known,
        avgRemaining: known > 0 ? sum / known : null,
        lowCount,
        bins,
        nextResetAt,
      })),
  };
}

/** One row for the whole pool, then one per raw plan type, largest plan first. */
export function summarizeAccountsByPlan(accounts: AccountSummary[], now = Date.now()): PlanOverview[] {
  if (accounts.length === 0) {
    return [];
  }
  const all = emptyGroup(null);
  const byPlan = new Map<string, GroupAccumulator>();
  for (const account of accounts) {
    addAccount(all, account, now);
    let group = byPlan.get(account.planType);
    if (!group) {
      group = emptyGroup(account.planType);
      byPlan.set(account.planType, group);
    }
    addAccount(group, account, now);
  }
  const plans = [...byPlan.values()].sort(
    (a, b) => b.total - a.total || (a.plan ?? "").localeCompare(b.plan ?? ""),
  );
  return [all, ...plans].map(finish);
}
