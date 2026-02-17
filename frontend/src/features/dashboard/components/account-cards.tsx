import { Users } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { AccountCard, type AccountCardProps } from "@/features/dashboard/components/account-card";
import type { AccountSummary } from "@/features/dashboard/schemas";

export type AccountCardsProps = {
  accounts: AccountSummary[];
  onAction?: AccountCardProps["onAction"];
};

export function AccountCards({ accounts, onAction }: AccountCardsProps) {
  if (accounts.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title="No accounts connected yet"
        description="Import or authenticate an account to get started."
      />
    );
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {accounts.map((account, index) => (
        <div key={account.accountId} className="animate-fade-in-up" style={{ animationDelay: `${index * 75}ms` }}>
          <AccountCard account={account} onAction={onAction} />
        </div>
      ))}
    </div>
  );
}
