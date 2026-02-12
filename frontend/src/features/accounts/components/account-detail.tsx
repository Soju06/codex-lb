import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
      <Card>
        <CardContent className="px-6 py-10 text-sm text-muted-foreground">
          Select an account to view details.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="gap-4 py-4">
      <CardHeader className="px-4">
        <CardTitle className="text-base">{account.email}</CardTitle>
        <p className="text-xs text-muted-foreground">{account.accountId}</p>
      </CardHeader>
      <CardContent className="space-y-3 px-4">
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
      </CardContent>
    </Card>
  );
}
