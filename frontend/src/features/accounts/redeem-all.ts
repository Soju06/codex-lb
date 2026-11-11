import type { AccountSummary } from "@/features/accounts/schemas";

/** Statuses for which the backend refuses to redeem a reset credit. */
const NON_REDEEMABLE_STATUSES = new Set(["paused", "reauth_required", "deactivated"]);

export type RedeemAllTarget = {
  accountId: string;
  label: string;
  email: string;
  availableResetCredits: number;
};

export type RedeemAllResult =
  | { accountId: string; ok: true }
  | { accountId: string; ok: false; error: string };

/**
 * Accounts that currently hold at least one banked reset credit and can redeem
 * it. Each account is only ever asked to redeem its own credit.
 */
export function getRedeemAllTargets(accounts: AccountSummary[]): RedeemAllTarget[] {
  return accounts
    .filter(
      (account) =>
        (account.availableResetCredits ?? 0) > 0 && !NON_REDEEMABLE_STATUSES.has(account.status),
    )
    .map((account) => ({
      accountId: account.accountId,
      label: account.displayName || account.email,
      email: account.email,
      availableResetCredits: account.availableResetCredits ?? 0,
    }));
}
