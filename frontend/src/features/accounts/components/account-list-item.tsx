import { cn } from "@/lib/utils";
import { StatusBadge } from "@/components/status-badge";
import type { AccountSummary } from "@/features/accounts/schemas";
import { normalizeStatus, quotaBarColor, quotaBarTrack } from "@/utils/account-status";
import { formatSlug } from "@/utils/formatters";

export type AccountListItemProps = {
  account: AccountSummary;
  selected: boolean;
  onSelect: (accountId: string) => void;
};

function MiniQuotaBar({ percent }: { percent: number }) {
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div className={cn("h-1 flex-1 overflow-hidden rounded-full", quotaBarTrack(clamped))}>
      <div
        className={cn("h-full rounded-full", quotaBarColor(clamped))}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

export function AccountListItem({ account, selected, onSelect }: AccountListItemProps) {
  const status = normalizeStatus(account.status);
  const title = account.displayName || account.email;
  const subtitle = account.displayName && account.displayName !== account.email
    ? account.email
    : formatSlug(account.planType);
  const secondary = account.usage?.secondaryRemainingPercent ?? 0;

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
          <p className="truncate text-sm font-medium">{title}</p>
          <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
          <p className="truncate font-mono text-[11px] text-muted-foreground/80" title={account.accountId}>
            ID {account.accountId}
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
