import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import type { AccountSummary } from "@/features/accounts/schemas";
import { normalizeStatus, quotaBarColor, quotaBarTrack } from "@/utils/account-status";
import { formatCompactAccountId } from "@/utils/account-identifiers";
import { isAnthropicAccountId, providerLabelForAccountId } from "@/utils/account-provider";
import { formatSlug } from "@/utils/formatters";

export type AccountListItemProps = {
  account: AccountSummary;
  selected: boolean;
  showAccountId?: boolean;
  onSelect: (accountId: string) => void;
};

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
  const status = normalizeStatus(account.status);
  const isAnthropic = isAnthropicAccountId(account.accountId);
  const title = account.displayName || account.email;
  const baseSubtitle = account.displayName && account.displayName !== account.email
    ? account.email
    : formatSlug(account.planType);
  const subtitle = showAccountId
    ? `${baseSubtitle} | ID ${formatCompactAccountId(account.accountId)}`
    : baseSubtitle;
  const secondary = account.usage?.secondaryRemainingPercent ?? null;

  return (
    <button
      type="button"
      onClick={() => onSelect(account.accountId)}
      className={cn(
        "w-full rounded-lg px-3 py-2.5 text-left transition-colors",
        isAnthropic && !selected && "hover:bg-amber-500/10",
        selected && isAnthropic && "bg-amber-500/15 ring-1 ring-amber-500/25",
        selected && !isAnthropic && "bg-primary/8 ring-1 ring-primary/25",
        !selected && !isAnthropic && "hover:bg-muted/50",
      )}
    >
      <div className="flex items-center gap-2.5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="min-w-0 truncate text-sm font-medium">{title}</p>
            <Badge
              variant="outline"
              className={cn(
                "h-5 px-1.5 text-[10px] uppercase tracking-wide",
                isAnthropic
                  ? "border-amber-500/30 text-amber-700 dark:text-amber-400"
                  : "border-sky-500/30 text-sky-700 dark:text-sky-400",
              )}
            >
              {providerLabelForAccountId(account.accountId)}
            </Badge>
          </div>
          <p className="truncate text-xs text-muted-foreground" title={showAccountId ? `Account ID ${account.accountId}` : undefined}>
            {subtitle}
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
