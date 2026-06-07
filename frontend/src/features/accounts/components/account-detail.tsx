import { User } from "lucide-react";

import { isEmailLabel } from "@/components/blur-email";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { AccountAliasForm } from "@/features/accounts/components/account-alias-form";
import { AccountActions } from "@/features/accounts/components/account-actions";
import { AccountProxyBinding } from "@/features/accounts/components/account-proxy-binding";
import { AccountTokenInfo } from "@/features/accounts/components/account-token-info";
import { AccountUsagePanel } from "@/features/accounts/components/account-usage-panel";
import type {
  AccountRoutingPolicy,
  AccountSummary,
} from "@/features/accounts/schemas";
import { useAccountTrends } from "@/features/accounts/hooks/use-accounts";
import type { AccountProxyBindingRequest, UpstreamProxyAdmin } from "@/features/settings/schemas";
import { formatCompactAccountId } from "@/utils/account-identifiers";
import { formatSlug } from "@/utils/formatters";

export type AccountDetailProps = {
  account: AccountSummary | null;
  showAccountId?: boolean;
  busy: boolean;
  onPause: (accountId: string) => void;
  onResume: (accountId: string) => void;
  onProbe: (accountId: string) => void;
  onSetAlias: (accountId: string, alias: string | null) => Promise<unknown>;
  onDelete: (accountId: string) => void;
  onReauth: () => void;
  onExportAuth: (accountId: string) => void;
  onLimitWarmupChange: (accountId: string, enabled: boolean) => void;
  onRoutingPolicyChange: (
    accountId: string,
    routingPolicy: AccountRoutingPolicy,
  ) => void;
  onSecurityWorkAuthorizedChange: (accountId: string, enabled: boolean) => void;
  upstreamProxyAdmin?: UpstreamProxyAdmin | null;
  onProxyBindingSave?: (accountId: string, payload: AccountProxyBindingRequest) => Promise<unknown>;
};

export function AccountDetail({
  account,
  showAccountId = false,
  busy,
  onPause,
  onResume,
  onProbe,
  onSetAlias,
  onDelete,
  onReauth,
  onExportAuth,
  onLimitWarmupChange,
  onRoutingPolicyChange,
  onSecurityWorkAuthorizedChange,
  upstreamProxyAdmin = null,
  onProxyBindingSave,
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
  const workspaceLabel = account.workspaceLabel || account.workspaceId || "Personal / unknown workspace";
  const seatLabel = account.seatType ? ` | ${formatSlug(account.seatType)}` : "";

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
        <p className="mt-0.5 text-xs text-muted-foreground">
          {workspaceLabel} | {formatSlug(account.planType)}{seatLabel}
        </p>
      </div>

      <AccountAliasForm account={account} busy={busy} onSetAlias={onSetAlias} />
      {onProxyBindingSave ? (
        <AccountProxyBinding
          account={account}
          admin={upstreamProxyAdmin}
          busy={busy}
          onSave={onProxyBindingSave}
        />
      ) : null}
      <AccountUsagePanel account={account} trends={trends} />
      <AccountTokenInfo account={account} />
      <AccountActions
        account={account}
        busy={busy}
        onPause={onPause}
        onResume={onResume}
        onProbe={onProbe}
        onDelete={onDelete}
        onReauth={onReauth}
        onExportAuth={onExportAuth}
        onLimitWarmupChange={onLimitWarmupChange}
        onRoutingPolicyChange={onRoutingPolicyChange}
        onSecurityWorkAuthorizedChange={onSecurityWorkAuthorizedChange}
      />
    </div>
  );
}
