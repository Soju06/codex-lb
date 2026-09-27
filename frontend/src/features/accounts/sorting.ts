import type { AccountSummary } from "@/features/accounts/schemas";
import type { AccountQuotaDisplayPreference } from "@/hooks/use-account-quota-display";
import { parseDate } from "@/utils/formatters";

export type AccountSortColumn =
  | "plan"
  | "subscription"
  | "quota_5h"
  | "quota_7d";

export type AccountSortMode =
  | "reset_soonest"
  | "reset_latest"
  | "name_asc"
  | "name_desc"
  | "most_reset_credits"
  | `${AccountSortColumn}_${"asc" | "desc"}`;

export const ACCOUNT_SORT_OPTIONS: readonly {
  value: AccountSortMode;
  label: string;
}[] = [
  { value: "reset_soonest", label: "Reset time (soonest)" },
  { value: "reset_latest", label: "Reset time (latest)" },
  { value: "most_reset_credits", label: "Most reset credits" },
  { value: "name_asc", label: "Name (A-Z)" },
  { value: "name_desc", label: "Name (Z-A)" },
  { value: "plan_asc", label: "Plan (A-Z)" },
  { value: "plan_desc", label: "Plan (Z-A)" },
  { value: "subscription_asc", label: "Subscription (soonest)" },
  { value: "subscription_desc", label: "Subscription (latest)" },
  { value: "quota_5h_asc", label: "5h quota (lowest remaining)" },
  { value: "quota_5h_desc", label: "5h quota (highest remaining)" },
  { value: "quota_7d_asc", label: "7d quota (lowest remaining)" },
  { value: "quota_7d_desc", label: "7d quota (highest remaining)" },
] as const;

export const DEFAULT_ACCOUNT_SORT_MODE: AccountSortMode = "most_reset_credits";

function visibleQuotaResetTimestamps(
  account: AccountSummary,
  quotaDisplay: AccountQuotaDisplayPreference,
): number[] {
  const now = Date.now();
  const hasPrimary =
    account.windowMinutesPrimary != null ||
    account.usage?.primaryRemainingPercent != null ||
    account.resetAtPrimary != null;
  const hasSecondary =
    account.windowMinutesSecondary != null ||
    account.usage?.secondaryRemainingPercent != null ||
    account.resetAtSecondary != null;
  const showPrimary =
    hasPrimary && (quotaDisplay !== "weekly" || !hasSecondary);
  const showSecondary = hasSecondary && (quotaDisplay !== "5h" || !hasPrimary);

  return [
    showPrimary
      ? (parseDate(account.resetAtPrimary)?.getTime() ??
        Number.POSITIVE_INFINITY)
      : Number.POSITIVE_INFINITY,
    showSecondary
      ? (parseDate(account.resetAtSecondary)?.getTime() ??
        Number.POSITIVE_INFINITY)
      : Number.POSITIVE_INFINITY,
  ].filter((resetAt) => resetAt > now);
}

function accountSortLabel(account: AccountSummary): string {
  return (account.displayName || account.email || account.accountId)
    .trim()
    .toLowerCase();
}

function accountResetTimestamp(
  account: AccountSummary,
  quotaDisplay: AccountQuotaDisplayPreference,
): number {
  const resets = visibleQuotaResetTimestamps(account, quotaDisplay);
  return resets.length > 0 ? Math.min(...resets) : Number.POSITIVE_INFINITY;
}

function compareKnownNumbers(
  left: number,
  right: number,
  direction: "asc" | "desc",
): number {
  const leftFinite = Number.isFinite(left);
  const rightFinite = Number.isFinite(right);
  if (leftFinite !== rightFinite) {
    return leftFinite ? -1 : 1;
  }
  if (!leftFinite || left === right) {
    return 0;
  }
  return direction === "desc" ? right - left : left - right;
}

function resetCreditNearestExpiry(account: AccountSummary): number {
  const parsed = parseDate(account.resetCreditNearestExpiresAt);
  return parsed ? parsed.getTime() : Number.POSITIVE_INFINITY;
}

function compareByResetCredits(
  left: AccountSummary,
  right: AccountSummary,
): number {
  const leftCount = left.availableResetCredits ?? 0;
  const rightCount = right.availableResetCredits ?? 0;
  if (leftCount !== rightCount) {
    return rightCount - leftCount;
  }
  // Tiebreak by soonest expiry ascending; null expiry (Infinity) sorts last.
  return compareKnownNumbers(
    resetCreditNearestExpiry(left),
    resetCreditNearestExpiry(right),
    "asc",
  );
}

function compareBySortMode(
  left: AccountSummary,
  right: AccountSummary,
  quotaDisplay: AccountQuotaDisplayPreference,
  sortMode: AccountSortMode,
): number {
  const direction =
    sortMode.endsWith("_desc") || sortMode === "reset_latest" ? "desc" : "asc";
  switch (sortMode) {
    case "most_reset_credits":
      return compareByResetCredits(left, right);
    case "reset_soonest":
    case "reset_latest":
      return compareKnownNumbers(
        accountResetTimestamp(left, quotaDisplay),
        accountResetTimestamp(right, quotaDisplay),
        direction,
      );
    case "name_asc":
    case "name_desc":
      return (
        accountSortLabel(left).localeCompare(accountSortLabel(right)) *
        (direction === "desc" ? -1 : 1)
      );
    case "plan_asc":
    case "plan_desc": {
      const leftPlan = left.planType.trim().toLowerCase();
      const rightPlan = right.planType.trim().toLowerCase();
      const leftKnown = leftPlan !== "" && leftPlan !== "unknown";
      const rightKnown = rightPlan !== "" && rightPlan !== "unknown";
      if (leftKnown !== rightKnown) return leftKnown ? -1 : 1;
      if (!leftKnown) return 0;
      return (
        leftPlan.localeCompare(rightPlan) * (direction === "desc" ? -1 : 1)
      );
    }
    case "subscription_asc":
    case "subscription_desc":
      return compareKnownNumbers(
        parseDate(left.subscription?.activeUntil)?.getTime() ?? Infinity,
        parseDate(right.subscription?.activeUntil)?.getTime() ?? Infinity,
        direction,
      );
    case "quota_5h_asc":
    case "quota_5h_desc":
      return compareKnownNumbers(
        left.usage?.primaryRemainingPercent ?? Infinity,
        right.usage?.primaryRemainingPercent ?? Infinity,
        direction,
      );
    case "quota_7d_asc":
    case "quota_7d_desc":
      return compareKnownNumbers(
        left.usage?.secondaryRemainingPercent ?? Infinity,
        right.usage?.secondaryRemainingPercent ?? Infinity,
        direction,
      );
  }
}

export function sortAccountsForDisplay(
  accounts: AccountSummary[],
  quotaDisplay: AccountQuotaDisplayPreference,
  sortMode: AccountSortMode = DEFAULT_ACCOUNT_SORT_MODE,
): AccountSummary[] {
  return accounts.slice().sort((left, right) => {
    const comparison = compareBySortMode(left, right, quotaDisplay, sortMode);
    if (comparison !== 0) return comparison;

    const leftReset = accountResetTimestamp(left, quotaDisplay);
    const rightReset = accountResetTimestamp(right, quotaDisplay);
    const resetComparison = compareKnownNumbers(leftReset, rightReset, "asc");
    if (resetComparison !== 0) {
      return resetComparison;
    }
    const labelComparison = accountSortLabel(left).localeCompare(
      accountSortLabel(right),
    );
    if (labelComparison !== 0) {
      return labelComparison;
    }
    return left.accountId.localeCompare(right.accountId);
  });
}
