import { cn } from "@/lib/utils";
import { isEmailLabel } from "@/components/blur-email";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { StatusBadge } from "@/components/status-badge";
import type { AccountSummary } from "@/features/accounts/schemas";
import { normalizeStatus, quotaBarColor, quotaBarTrack } from "@/utils/account-status";
import { formatCompactAccountId } from "@/utils/account-identifiers";
import { formatProviderLabel, formatSlug } from "@/utils/formatters";

export type AccountListItemProps = {
  account: AccountSummary;
  selected: boolean;
  showAccountId?: boolean;
  onSelect: (accountId: string) => void;
};

function formatAccountListSubtitle(account: AccountSummary): string {
  if (account.providerKind === "openai_platform") {
    return `${formatProviderLabel(account.providerKind)} API key`;
  }
  return formatSlug(account.planType);
}

function MiniQuotaBar({ percent }: { percent: number | null }) {
  if (percent === null) {
    return <div data-testid="mini-quota-track" className="h-1 flex-1 overflow-hidden rounded-full bg-muted" />;
  }
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div data-testid="mini-quota-track" className={cn("h-1 flex-1 overflow-hidden rounded-full", quotaBarTrack(clamped))}>
      <div
        data-testid="mini-quota-fill"
        className={cn("h-full rounded-full", quotaBarColor(clamped))}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

export function AccountListItem({ account, selected, showAccountId = false, onSelect }: AccountListItemProps) {
  const blurred = usePrivacyStore((s) => s.blurred);
  const status = normalizeStatus(account.status);
  const title = account.displayName || account.email;
  const titleIsEmail = isEmailLabel(title, account.email);
  const emailSubtitle = account.displayName && account.displayName !== account.email
    ? account.email
    : null;
  const baseSubtitle = formatAccountListSubtitle(account);
  const providerMetadata = account.providerKind === "openai_platform" && account.routingSubjectId
    ? `${baseSubtitle} | Subject ${account.routingSubjectId}`
    : baseSubtitle;
  const shouldAppendProviderMetadata = account.providerKind === "openai_platform";
  const idSuffix = showAccountId ? ` | ID ${formatCompactAccountId(account.accountId)}` : "";
  const secondary = account.providerKind === "openai_platform"
    ? null
    : account.usage?.secondaryRemainingPercent ?? null;

  return (
    <button
      type="button"
      onClick={() => onSelect(account.accountId)}
      className={cn(
        "w-full rounded-lg px-3 py-2.5 text-left transition-colors",
        selected
          ? "bg-primary/8 ring-1 ring-primary/25"
          : "hover:bg-muted/50",
      )}
    >
      <div className="flex items-center gap-2.5">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">
            {titleIsEmail && blurred ? <span className="privacy-blur">{title}</span> : title}
          </p>
          <p className="truncate text-xs text-muted-foreground" title={showAccountId ? `Account ID ${account.accountId}` : undefined}>
            {emailSubtitle ? (
              <>
                <span className={blurred ? "privacy-blur" : undefined}>{emailSubtitle}</span>
                {idSuffix}
                {shouldAppendProviderMetadata ? ` | ${providerMetadata}` : ""}
              </>
            ) : (
              <>{providerMetadata}{idSuffix}</>
            )}
          </p>
        </div>
        <StatusBadge status={status} />
      </div>
      <div className="mt-1.5">
        <MiniQuotaBar percent={secondary} />
      </div>
    </button>
  );
}
