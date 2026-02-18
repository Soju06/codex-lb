import { User } from "lucide-react";

import { AccountActions } from "@/features/accounts/components/account-actions";
import { AccountTokenInfo } from "@/features/accounts/components/account-token-info";
import { AccountUsagePanel } from "@/features/accounts/components/account-usage-panel";
import type { AccountSummary } from "@/features/accounts/schemas";
import { useAccountTrends } from "@/features/accounts/hooks/use-accounts";
import { formatCompactAccountId } from "@/utils/account-identifiers";

export type AccountDetailProps = {
  account: AccountSummary | null;
  showAccountId?: boolean;
  busy: boolean;
  onPause: (accountId: string) => void;
  onResume: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  onReauth: () => void;
};

export function AccountDetail({
  account,
  showAccountId = false,
  busy,
  onPause,
  onResume,
  onDelete,
  onReauth,
}: AccountDetailProps) {
  const { data: trends } = useAccountTrends(account?.accountId ?? null);

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

  const title = account.displayName || account.email;
  const compactId = formatCompactAccountId(account.accountId);
  const emailSubtitle = account.displayName && account.displayName !== account.email
    ? account.email
    : null;
  const heading = showAccountId && !emailSubtitle ? `${title} (${compactId})` : title;
  const subtitle = showAccountId && emailSubtitle ? `${emailSubtitle} | ID ${compactId}` : emailSubtitle;

  return (
    <div key={account.accountId} className="animate-fade-in-up space-y-4 rounded-xl border bg-card p-5">
      {/* Account header */}
      <div>
        <h2 className="text-base font-semibold">
          {heading}
        </h2>
        {subtitle ? (
          <p className="mt-0.5 text-xs text-muted-foreground" title={showAccountId ? `Account ID ${account.accountId}` : undefined}>
            {subtitle}
          </p>
        ) : null}
      </div>

      <AccountUsagePanel account={account} trends={trends} />
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
