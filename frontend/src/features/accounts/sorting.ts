import type { AccountSummary } from "@/features/accounts/schemas";
import type { AccountQuotaDisplayPreference } from "@/hooks/use-account-quota-display";
import { parseDate } from "@/utils/formatters";

export type AccountSortMode = "reset_soonest" | "reset_latest" | "name_asc" | "name_desc";

export const ACCOUNT_SORT_OPTIONS: readonly { value: AccountSortMode; label: string }[] = [
  { value: "reset_soonest", label: "Reset time (soonest)" },
  { value: "reset_latest", label: "Reset time (latest)" },
  { value: "name_asc", label: "Name (A-Z)" },
  { value: "name_desc", label: "Name (Z-A)" },
] as const;

export const DEFAULT_ACCOUNT_SORT_MODE: AccountSortMode = "reset_soonest";

function visibleQuotaResetTimestamps(
  account: AccountSummary,
  quotaDisplay: AccountQuotaDisplayPreference,
): number[] {
  const now = Date.now();
  const hasPrimary = account.windowMinutesPrimary != null || account.usage?.primaryRemainingPercent != null || account.resetAtPrimary != null;
  const hasSecondary = account.windowMinutesSecondary != null || account.usage?.secondaryRemainingPercent != null || account.resetAtSecondary != null;
  const showPrimary = hasPrimary && (quotaDisplay !== "weekly" || !hasSecondary);
  const showSecondary = hasSecondary && (quotaDisplay !== "5h" || !hasPrimary);

  return [
    showPrimary ? parseDate(account.resetAtPrimary)?.getTime() ?? Number.POSITIVE_INFINITY : Number.POSITIVE_INFINITY,
    showSecondary ? parseDate(account.resetAtSecondary)?.getTime() ?? Number.POSITIVE_INFINITY : Number.POSITIVE_INFINITY,
  ].filter((resetAt) => resetAt > now);
}

function accountSortLabel(account: AccountSummary): string {
  return (account.displayName || account.email || account.accountId).trim().toLowerCase();
}

function accountResetTimestamp(account: AccountSummary, quotaDisplay: AccountQuotaDisplayPreference): number {
  const resets = visibleQuotaResetTimestamps(account, quotaDisplay);
  return resets.length > 0 ? Math.min(...resets) : Number.POSITIVE_INFINITY;
}

function compareResetTimestamps(leftReset: number, rightReset: number, direction: "asc" | "desc"): number {
  const leftFinite = Number.isFinite(leftReset);
  const rightFinite = Number.isFinite(rightReset);
  if (leftFinite !== rightFinite) {
    return leftFinite ? -1 : 1;
  }
  if (leftReset === rightReset) {
    return 0;
  }
  return direction === "desc" ? rightReset - leftReset : leftReset - rightReset;
}

export function sortAccountsForDisplay(
  accounts: AccountSummary[],
  quotaDisplay: AccountQuotaDisplayPreference,
  sortMode: AccountSortMode = DEFAULT_ACCOUNT_SORT_MODE,
): AccountSummary[] {
  return accounts
    .slice()
    .sort((left, right) => {
      if (sortMode === "reset_latest" || sortMode === "reset_soonest") {
        const leftReset = accountResetTimestamp(left, quotaDisplay);
        const rightReset = accountResetTimestamp(right, quotaDisplay);
        const resetComparison = compareResetTimestamps(
          leftReset,
          rightReset,
          sortMode === "reset_latest" ? "desc" : "asc",
        );
        if (resetComparison !== 0) {
          return resetComparison;
        }
      } else {
        const leftLabel = accountSortLabel(left);
        const rightLabel = accountSortLabel(right);
        const labelComparison = sortMode === "name_asc"
          ? leftLabel.localeCompare(rightLabel)
          : rightLabel.localeCompare(leftLabel);
        if (labelComparison !== 0) {
          return labelComparison;
        }
      }

      const leftReset = accountResetTimestamp(left, quotaDisplay);
      const rightReset = accountResetTimestamp(right, quotaDisplay);
      const resetComparison = compareResetTimestamps(leftReset, rightReset, "asc");
      if (resetComparison !== 0) {
        return resetComparison;
      }
      const labelComparison = accountSortLabel(left).localeCompare(accountSortLabel(right));
      if (labelComparison !== 0) {
        return labelComparison;
      }
      return left.accountId.localeCompare(right.accountId);
    });
}
