import { useMemo } from "react";
import { Users } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { AccountCard, type AccountCardProps } from "@/features/dashboard/components/account-card";
import type { AccountSummary } from "@/features/dashboard/schemas";
import { buildDuplicateAccountIdSet } from "@/utils/account-identifiers";

const ACCOUNT_CARD_VISIBLE_ROWS = 2;
// Account cards can grow when the optional email row is rendered.
const ACCOUNT_CARD_ROW_HEIGHT_REM = 12.5;
const ACCOUNT_CARD_ROW_GAP_REM = 1;

export type AccountCardsProps = {
  accounts: AccountSummary[];
  onAction?: AccountCardProps["onAction"];
};

export function AccountCards({ accounts, onAction }: AccountCardsProps) {
  const duplicateAccountIds = useMemo(() => buildDuplicateAccountIdSet(accounts), [accounts]);

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
    <div
      data-testid="dashboard-account-cards"
      className="grid gap-4 overflow-y-auto pr-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden sm:grid-cols-2 lg:grid-cols-3"
      style={{
        maxHeight: `calc(${ACCOUNT_CARD_VISIBLE_ROWS} * ${ACCOUNT_CARD_ROW_HEIGHT_REM}rem + ${(ACCOUNT_CARD_VISIBLE_ROWS - 1) * ACCOUNT_CARD_ROW_GAP_REM}rem)`,
      }}
    >
      {accounts.map((account, index) => (
        <div key={account.accountId} className="animate-fade-in-up" style={{ animationDelay: `${index * 75}ms` }}>
          <AccountCard
            account={account}
            showAccountId={duplicateAccountIds.has(account.accountId)}
            onAction={onAction}
          />
        </div>
      ))}
    </div>
  );
}
