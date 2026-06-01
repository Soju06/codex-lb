import { User } from "lucide-react";

import { isEmailLabel } from "@/components/blur-email";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { AccountAliasForm } from "@/features/accounts/components/account-alias-form";
import { AccountActions } from "@/features/accounts/components/account-actions";
import { AccountTokenInfo } from "@/features/accounts/components/account-token-info";
import { AccountUsagePanel } from "@/features/accounts/components/account-usage-panel";
import type {
  AccountRoutingPolicy,
  AccountSummary,
} from "@/features/accounts/schemas";
import { useAccountTrends } from "@/features/accounts/hooks/use-accounts";
import { formatCompactAccountId } from "@/utils/account-identifiers";

export type AccountDetailProps = {
  account: AccountSummary | null;
  showAccountId?: boolean;
  busy: boolean;
  onPause: (accountId: string) => void;
  onResume: (accountId: string) => void;
  onSetAlias: (accountId: string, alias: string | null) => Promise<unknown>;
  onDelete: (accountId: string) => void;
  onReauth: () => void;
  onExport: (accountId: string) => void;
  onLimitWarmupChange: (accountId: string, enabled: boolean) => void;
  onRoutingPolicyChange: (
    accountId: string,
    routingPolicy: AccountRoutingPolicy,
  ) => void;
  onExportOpenCodeAuth: (accountId: string) => void;
  onSecurityWorkAuthorizedChange: (accountId: string, enabled: boolean) => void;
};

export function AccountDetail({
  account,
  showAccountId = false,
  busy,
  onPause,
  onResume,
  onSetAlias,
  onDelete,
  onReauth,
  onExport,
  onLimitWarmupChange,
  onRoutingPolicyChange,
  onExportOpenCodeAuth,
  onSecurityWorkAuthorizedChange,
}: AccountDetailProps) {
  const { data: trends } = useAccountTrends(account?.accountId ?? null);
  const blurred = usePrivacyStore((s) => s.blurred);

  if (!account) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed p-12">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-muted">
          <User className="h-5 w-5 text-muted-foreground" />
        </div>
        <p className="mt-3 text-sm font-medium text-muted-foreground">
          Select an account
        </p>
        <p className="mt-1 text-xs text-muted-foreground/70">
          Choose an account from the list to view details.
        </p>
      </div>
    );
  }

  const title = account.displayName || account.email;
  const titleIsEmail = isEmailLabel(title, account.email);
  const compactId = formatCompactAccountId(account.accountId);
  const emailSubtitle =
    account.displayName && account.displayName !== account.email
      ? account.email
      : null;
  const idSuffix = showAccountId ? ` (${compactId})` : "";

  return (
    <div
      key={account.accountId}
      className="animate-fade-in-up space-y-4 rounded-xl border bg-card p-5"
    >
      {/* Account header */}
      <div>
        <h2 className="text-base font-semibold">
          {titleIsEmail ? (
            <>
              <span className={blurred ? "privacy-blur" : ""}>{title}</span>
              {idSuffix}
            </>
          ) : (
            <>
              {title}
              {!emailSubtitle ? idSuffix : ""}
            </>
          )}
        </h2>
        {emailSubtitle ? (
          <p
            className="mt-0.5 text-xs text-muted-foreground"
            title={
              showAccountId ? `Account ID ${account.accountId}` : undefined
            }
          >
            <span className={blurred ? "privacy-blur" : ""}>
              {emailSubtitle}
            </span>
            {showAccountId ? ` | ID ${compactId}` : ""}
          </p>
        ) : null}
      </div>

      <AccountAliasForm account={account} busy={busy} onSetAlias={onSetAlias} />
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-muted/20 px-3 py-2">
        <div>
          <p className="text-xs font-medium text-foreground">Routing policy</p>
          <p className="text-xs text-muted-foreground">
            {account.routingPolicy === "burn_first"
              ? "Prefer this account for opportunistic burn."
              : account.routingPolicy === "preserve"
                ? "Preserve this account from opportunistic burn."
                : "Use normal routing for this account."}
          </p>
        </div>
        <Select
          value={account.routingPolicy ?? "normal"}
          onValueChange={(value) =>
            onRoutingPolicyChange(
              account.accountId,
              value as AccountRoutingPolicy,
            )
          }
          disabled={busy}
        >
          <SelectTrigger
            size="sm"
            className="w-36 text-xs"
            aria-label="Routing policy"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent align="end">
            <SelectItem value="normal">Normal</SelectItem>
            <SelectItem value="burn_first">Burn first</SelectItem>
            <SelectItem value="preserve">Preserve</SelectItem>
          </SelectContent>
        </Select>
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
        onExport={onExport}
        onLimitWarmupChange={onLimitWarmupChange}
        onExportOpenCodeAuth={onExportOpenCodeAuth}
        onRoutingPolicyChange={onRoutingPolicyChange}
        onSecurityWorkAuthorizedChange={onSecurityWorkAuthorizedChange}
      />
    </div>
  );
}
