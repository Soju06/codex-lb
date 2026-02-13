import { AccountCard } from "@/features/dashboard/components/account-card";
import type { AccountSummary } from "@/features/dashboard/schemas";

export type AccountCardsProps = {
  accounts: AccountSummary[];
};

export function AccountCards({ accounts }: AccountCardsProps) {
  if (accounts.length === 0) {
    return (
      <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
        No accounts connected yet.
      </div>
    );
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {accounts.map((account) => (
        <AccountCard key={account.accountId} account={account} />
      ))}
    </div>
  );
}
