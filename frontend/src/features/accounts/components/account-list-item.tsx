import { cn } from "@/lib/utils";
import { StatusBadge } from "@/components/status-badge";
import type { AccountSummary } from "@/features/accounts/schemas";
import { normalizeStatus } from "@/utils/account-status";
import { formatSlug } from "@/utils/formatters";

export type AccountListItemProps = {
  account: AccountSummary;
  selected: boolean;
  onSelect: (accountId: string) => void;
};

export function AccountListItem({ account, selected, onSelect }: AccountListItemProps) {
  const status = normalizeStatus(account.status);
  const title = account.displayName || account.email;
  const subtitle = account.displayName && account.displayName !== account.email
    ? account.email
    : formatSlug(account.planType);

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
        </div>
        <StatusBadge status={status} />
      </div>
    </button>
  );
}
