import { User } from "lucide-react";

import { AccountActions } from "@/features/accounts/components/account-actions";
import { AccountTokenInfo } from "@/features/accounts/components/account-token-info";
import { AccountUsagePanel } from "@/features/accounts/components/account-usage-panel";
import type { AccountSummary } from "@/features/accounts/schemas";

export type AccountDetailProps = {
  account: AccountSummary | null;
  busy: boolean;
  onPause: (accountId: string) => void;
  onResume: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  onReauth: () => void;
};

export function AccountDetail({
  account,
  busy,
  onPause,
  onResume,
  onDelete,
  onReauth,
}: AccountDetailProps) {
  if (!account) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed p-12">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-muted">
          <User className="h-5 w-5 text-muted-foreground" />
        </div>
        <p className="mt-3 text-sm font-medium text-muted-foreground">Select an account</p>
        <p className="mt-1 text-xs text-muted-foreground/70">Choose an account from the list to view details.</p>
      </div>
    );
  }

  return (
    <div key={account.accountId} className="animate-fade-in-up space-y-4 rounded-xl border bg-card p-5">
      {/* Account header */}
      <div>
        <h2 className="text-base font-semibold">
          {account.displayName || account.email}
        </h2>
        {account.displayName && account.displayName !== account.email && (
          <p className="mt-0.5 text-xs text-muted-foreground">{account.email}</p>
        )}
      </div>

      <AccountUsagePanel account={account} />
      <AccountTokenInfo account={account} />
      <AccountActions
        account={account}
        busy={busy}
        onPause={onPause}
        onResume={onResume}
        onDelete={onDelete}
        onReauth={onReauth}
      />
    </div>
  );
}
