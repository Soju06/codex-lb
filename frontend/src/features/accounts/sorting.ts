import type { AccountSummary } from "@/features/accounts/schemas";
import { accountPriorityRank } from "@/features/accounts/priority";
import { parseDate } from "@/utils/formatters";
import type { AccountQuotaDisplayPreference } from "@/hooks/use-account-quota-display";

function visibleQuotaResetTimestamps(
  account: AccountSummary,
  quotaDisplay: AccountQuotaDisplayPreference,
): number[] {
  const now = Date.now();
  const showPrimary = account.windowMinutesPrimary != null && (quotaDisplay !== "weekly" || account.windowMinutesSecondary == null);
  const showSecondary = account.windowMinutesSecondary != null && (quotaDisplay !== "5h" || account.windowMinutesPrimary == null);

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

export function sortAccountsForDisplay(
  accounts: AccountSummary[],
  quotaDisplay: AccountQuotaDisplayPreference,
  prioritiesEnabled = true,
): AccountSummary[] {
  return accounts
    .slice()
    .sort((left, right) => {
      if (prioritiesEnabled) {
        const leftPriority = accountPriorityRank(left.priority);
        const rightPriority = accountPriorityRank(right.priority);
        if (leftPriority !== rightPriority) {
          return leftPriority - rightPriority;
        }
      }
      const leftReset = accountResetTimestamp(left, quotaDisplay);
      const rightReset = accountResetTimestamp(right, quotaDisplay);
      if (leftReset !== rightReset) {
        return leftReset - rightReset;
      }
      const labelComparison = accountSortLabel(left).localeCompare(accountSortLabel(right));
      if (labelComparison !== 0) {
        return labelComparison;
      }
      return left.accountId.localeCompare(right.accountId);
    });
}
