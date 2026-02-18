import type { AccountSummary } from "@/features/accounts/schemas";
import {
  formatAccessTokenLabel,
  formatIdTokenLabel,
  formatRefreshTokenLabel,
} from "@/utils/formatters";

export type AccountTokenInfoProps = {
  account: AccountSummary;
};

export function AccountTokenInfo({ account }: AccountTokenInfoProps) {
  return (
    <div className="space-y-3 rounded-lg border bg-muted/30 p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Token Status</h3>
      <dl className="space-y-2 text-xs">
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Access</dt>
          <dd className="font-medium">{formatAccessTokenLabel(account.auth)}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Refresh</dt>
          <dd className="font-medium">{formatRefreshTokenLabel(account.auth)}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">ID token</dt>
          <dd className="font-medium">{formatIdTokenLabel(account.auth)}</dd>
        </div>
      </dl>
    </div>
  );
}
