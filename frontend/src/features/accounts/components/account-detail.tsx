import { User } from "lucide-react";

import { isEmailLabel } from "@/components/blur-email";
import { StatusBadge } from "@/components/status-badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { AccountActions } from "@/features/accounts/components/account-actions";
import { AccountPriorityBadge } from "@/features/accounts/components/account-priority-badge";
import { AccountTokenInfo } from "@/features/accounts/components/account-token-info";
import { AccountUsagePanel } from "@/features/accounts/components/account-usage-panel";
import type { AccountSummary } from "@/features/accounts/schemas";
import { useAccountTrends } from "@/features/accounts/hooks/use-accounts";
import { ACCOUNT_PRIORITY_OPTIONS, type AccountPriority } from "@/features/accounts/priority";
import { normalizeStatus } from "@/utils/account-status";
import { formatCompactAccountId } from "@/utils/account-identifiers";

export type AccountDetailProps = {
  account: AccountSummary | null;
  showAccountId?: boolean;
  busy: boolean;
  prioritiesEnabled?: boolean;
  onPause: (accountId: string) => void;
  onResume: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  onReauth: () => void;
  onPriorityChange: (accountId: string, priority: AccountPriority) => Promise<void>;
};

export function AccountDetail({
  account,
  showAccountId = false,
  busy,
  prioritiesEnabled = true,
  onPause,
  onResume,
  onDelete,
  onReauth,
  onPriorityChange,
}: AccountDetailProps) {
  const { data: trends } = useAccountTrends(account?.accountId ?? null);
  const blurred = usePrivacyStore((s) => s.blurred);

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
  const titleIsEmail = isEmailLabel(title, account.email);
  const compactId = formatCompactAccountId(account.accountId);
  const emailSubtitle = account.displayName && account.displayName !== account.email
    ? account.email
    : null;
  const idSuffix = showAccountId ? ` (${compactId})` : "";

  return (
    <div key={account.accountId} className="animate-fade-in-up space-y-4 rounded-xl border bg-card p-5">
      {/* Account header */}
      <div>
        <h2 className="text-base font-semibold">
          {titleIsEmail ? <><span className={blurred ? "privacy-blur" : ""}>{title}</span>{idSuffix}</> : <>{title}{!emailSubtitle ? idSuffix : ""}</>}
        </h2>
        {emailSubtitle ? (
          <p className="mt-0.5 text-xs text-muted-foreground" title={showAccountId ? `Account ID ${account.accountId}` : undefined}>
            <span className={blurred ? "privacy-blur" : ""}>{emailSubtitle}</span>{showAccountId ? ` | ID ${compactId}` : ""}
          </p>
        ) : null}
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <StatusBadge status={normalizeStatus(account.status)} />
          {prioritiesEnabled ? <AccountPriorityBadge priority={account.priority} /> : null}
        </div>
      </div>

      {prioritiesEnabled ? (
        <div className="rounded-lg border bg-muted/30 p-4">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Priority</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Higher priority accounts are preferred when multiple eligible accounts are available.
              </p>
            </div>
            <Select
              value={account.priority}
              onValueChange={(value) => {
                const nextPriority = value as AccountPriority;
                if (nextPriority !== account.priority) {
                  void onPriorityChange(account.accountId, nextPriority);
                }
              }}
              disabled={busy}
            >
              <SelectTrigger className="h-8 w-36 text-xs" disabled={busy}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                {ACCOUNT_PRIORITY_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      ) : null}

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
