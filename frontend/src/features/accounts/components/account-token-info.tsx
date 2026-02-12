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
    <div className="space-y-2 rounded-lg border p-3">
      <h3 className="text-sm font-semibold">Token Status</h3>
      <dl className="space-y-1 text-xs">
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Access</dt>
          <dd>{formatAccessTokenLabel(account.auth)}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">Refresh</dt>
          <dd>{formatRefreshTokenLabel(account.auth)}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground">ID token</dt>
          <dd>{formatIdTokenLabel(account.auth)}</dd>
        </div>
      </dl>
    </div>
  );
}
