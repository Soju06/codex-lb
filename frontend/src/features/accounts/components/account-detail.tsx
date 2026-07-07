import { Check, Pencil, User, X } from "lucide-react";
import { useState } from "react";

import { isEmailLabel } from "@/components/blur-email";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { AccountActions } from "@/features/accounts/components/account-actions";
import { AccountProxyBinding } from "@/features/accounts/components/account-proxy-binding";
import { AccountTokenInfo } from "@/features/accounts/components/account-token-info";
import { AccountUsagePanel } from "@/features/accounts/components/account-usage-panel";
import type {
  AccountRoutingPolicy,
  AccountSummary,
  AccountUsageResetCredits,
} from "@/features/accounts/schemas";
import { useAccountTrends } from "@/features/accounts/hooks/use-accounts";
import type {
  AccountProxyBindingRequest,
  UpstreamProxyAdmin,
  UpstreamProxyEndpointTestResponse,
} from "@/features/settings/schemas";
import { formatCompactAccountId } from "@/utils/account-identifiers";
import { formatSlug } from "@/utils/formatters";

export type AccountDetailProps = {
  account: AccountSummary | null;
  showAccountId?: boolean;
  busy: boolean;
  readOnly?: boolean;
  onPause: (accountId: string) => void;
  onResume: (accountId: string) => void;
  onProbe: (accountId: string) => void;
  onResetUsage: (accountId: string) => void;
  onSetAlias: (accountId: string, alias: string | null) => Promise<unknown>;
  onDelete: (accountId: string) => void;
  onReauth: () => void;
  onExportAuth: (accountId: string) => void;
  onResetCredit: (accountId: string) => void;
  onLimitWarmupChange: (accountId: string, enabled: boolean) => void;
  onRoutingPolicyChange: (
    accountId: string,
    routingPolicy: AccountRoutingPolicy,
  ) => void;
  onSecurityWorkAuthorizedChange: (accountId: string, enabled: boolean) => void;
  upstreamProxyAdmin?: UpstreamProxyAdmin | null;
  onProxyBindingSave?: (accountId: string, payload: AccountProxyBindingRequest) => Promise<unknown>;
  onProxyEndpointTest?: (endpointId: string) => Promise<UpstreamProxyEndpointTestResponse>;
  resetCredits?: AccountUsageResetCredits | null;
  resetCreditsLoading?: boolean;
  resetCreditsUnavailable?: boolean;
};

export function AccountDetail({
  account,
  showAccountId = false,
  busy,
  readOnly = false,
  onPause,
  onResume,
  onProbe,
  onResetUsage,
  onSetAlias,
  onDelete,
  onReauth,
  onExportAuth,
  onResetCredit,
  onLimitWarmupChange,
  onRoutingPolicyChange,
  onSecurityWorkAuthorizedChange,
  upstreamProxyAdmin = null,
  onProxyBindingSave,
  onProxyEndpointTest,
  resetCredits = null,
  resetCreditsLoading = false,
  resetCreditsUnavailable = false,
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

  const aliasLabel = account.alias?.trim() ?? "";
  const localLabel = aliasLabel || account.displayName || account.email;
  const labelIsEmail = !aliasLabel && isEmailLabel(localLabel, account.email);
  const compactId = formatCompactAccountId(account.accountId);
  const emailSubtitle =
    aliasLabel || (account.displayName && account.displayName !== account.email)
      ? account.email
      : null;
  const idSuffix = showAccountId ? ` (${compactId})` : "";
  const workspaceLabel = account.chatgptAccountId || account.workspaceLabel || account.workspaceId || "Personal / unknown workspace";
  const seatLabel = account.seatType ? ` | ${formatSlug(account.seatType)}` : "";
  const operatorRecoveryAction =
    account.status === "reauth_required" || account.status === "deactivated";
  const usageResetDisabled =
    busy || readOnly || account.status === "paused" || operatorRecoveryAction || (resetCredits?.availableCount ?? 0) <= 0;

  return (
    <div
      key={account.accountId}
      className="animate-fade-in-up min-w-0 space-y-4 rounded-xl border bg-card p-4 sm:p-5"
    >
      {/* Account header */}
      <div>
        <AccountNameField
          key={account.accountId}
          accountId={account.accountId}
          alias={account.alias ?? null}
          localLabel={localLabel}
          labelIsEmail={labelIsEmail}
          idSuffix={emailSubtitle ? "" : idSuffix}
          blurred={blurred}
          busy={busy}
          readOnly={readOnly}
          onSetAlias={onSetAlias}
        />
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

      {onProxyBindingSave ? (
        <AccountProxyBinding
          account={account}
          admin={upstreamProxyAdmin}
          busy={busy}
          readOnly={readOnly}
          onSave={onProxyBindingSave}
          onTestEndpoint={onProxyEndpointTest}
        />
      ) : null}
      <AccountUsagePanel
        account={account}
        trends={trends}
        resetCredits={resetCredits}
        resetCreditsLoading={resetCreditsLoading}
        resetCreditsUnavailable={resetCreditsUnavailable}
        resetDisabled={usageResetDisabled}
        onReset={onResetUsage}
      />
      <AccountTokenInfo account={account} />
      <AccountActions
        account={account}
        busy={busy}
        readOnly={readOnly}
        onPause={onPause}
        onResume={onResume}
        onProbe={onProbe}
        onDelete={onDelete}
        onReauth={onReauth}
        onExportAuth={onExportAuth}
        onResetCredit={onResetCredit}
        onLimitWarmupChange={onLimitWarmupChange}
        onRoutingPolicyChange={onRoutingPolicyChange}
        onSecurityWorkAuthorizedChange={onSecurityWorkAuthorizedChange}
      />
    </div>
  );
}

type AccountNameFieldProps = {
  accountId: string;
  alias: string | null;
  localLabel: string;
  labelIsEmail: boolean;
  idSuffix: string;
  blurred: boolean;
  busy: boolean;
  readOnly: boolean;
  onSetAlias: (accountId: string, alias: string | null) => Promise<unknown>;
};

function AccountNameField({
  accountId,
  alias,
  localLabel,
  labelIsEmail,
  idSuffix,
  blurred,
  busy,
  readOnly,
  onSetAlias,
}: AccountNameFieldProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [aliasDraft, setAliasDraft] = useState(alias ?? "");

  const handleSave = async () => {
    const trimmed = aliasDraft.trim();
    await onSetAlias(accountId, trimmed === "" ? null : trimmed);
    setIsEditing(false);
  };

  const handleCancel = () => {
    setAliasDraft(alias ?? "");
    setIsEditing(false);
  };

  if (isEditing) {
    return (
      <div className="space-y-1.5">
        <div className="flex items-center gap-1.5">
          <Input
            id="account-alias"
            aria-label="Account alias"
            className="h-8 text-sm"
            maxLength={255}
            placeholder="Personal Plus"
            value={aliasDraft}
            autoFocus
            disabled={busy || readOnly}
            onChange={(event) => setAliasDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void handleSave();
              } else if (event.key === "Escape") {
                event.preventDefault();
                handleCancel();
              }
            }}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Save alias"
            disabled={busy || readOnly}
            onClick={() => void handleSave()}
          >
            <Check className="size-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Cancel"
            onClick={handleCancel}
          >
            <X className="size-4" />
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Use a local label to distinguish accounts that share the same email.
        </p>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <h2 className="min-w-0 truncate text-base font-semibold">
        {labelIsEmail ? (
          <>
            <span className={cn(blurred && "privacy-blur")}>{localLabel}</span>
            {idSuffix}
          </>
        ) : (
          <>
            {localLabel}
            {idSuffix}
          </>
        )}
      </h2>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        aria-label="Edit alias"
        title="Use a local label to distinguish accounts that share the same email."
        disabled={busy || readOnly}
        onClick={() => {
          setAliasDraft(alias ?? "");
          setIsEditing(true);
        }}
      >
        <Pencil className="size-3.5" />
      </Button>
    </div>
  );
}
