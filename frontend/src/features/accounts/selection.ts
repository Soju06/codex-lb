import type { AccountQuotaDisplayPreference } from "@/hooks/use-account-quota-display";
import type { AccountSummary } from "@/features/accounts/schemas";
import { sortAccountsForDisplay } from "@/features/accounts/sorting";

export function resolveSelectedAccountId(
  accounts: AccountSummary[],
  quotaDisplay: AccountQuotaDisplayPreference,
  selectedAccountId: string | null,
): string | null {
  if (selectedAccountId && accounts.some((account) => account.accountId === selectedAccountId)) {
    return selectedAccountId;
  }
  if (accounts.length === 0) {
    return null;
  }
  return sortAccountsForDisplay(accounts, quotaDisplay)[0]?.accountId ?? null;
}
