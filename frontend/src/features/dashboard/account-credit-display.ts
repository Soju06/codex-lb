import type { AccountSummary } from "@/features/dashboard/schemas";

export function accountSubscriptionCredits(account: AccountSummary): number | null {
  const monthlyOnly =
    account.windowMinutesMonthly != null &&
    account.windowMinutesPrimary == null &&
    account.windowMinutesSecondary == null;
  const weeklyOnly = account.windowMinutesPrimary == null && account.windowMinutesSecondary != null;

  const value = monthlyOnly
    ? account.remainingCreditsMonthly
    : weeklyOnly
      ? account.remainingCreditsSecondary
      : (account.remainingCreditsSecondary ?? account.remainingCreditsPrimary);

  return value ?? null;
}

export function formatCreditValue(value: number | null | undefined): string {
  return value == null ? "-" : value.toFixed(2);
}

export function formatPurchasedCredits(account: AccountSummary, unlimitedLabel: string): string {
  if (account.creditsUnlimited) {
    return unlimitedLabel;
  }
  return formatCreditValue(account.creditsBalance);
}
