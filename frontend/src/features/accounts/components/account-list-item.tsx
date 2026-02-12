import { cn } from "@/lib/utils";
import { StatusBadge } from "@/components/status-badge";
import type { AccountSummary } from "@/features/accounts/schemas";

type DashboardAccountStatus = "active" | "paused" | "limited" | "exceeded" | "deactivated";

function normalizeStatus(status: string): DashboardAccountStatus {
  if (status === "paused") {
    return "paused";
  }
  if (status === "rate_limited") {
    return "limited";
  }
  if (status === "quota_exceeded") {
    return "exceeded";
  }
  if (status === "deactivated") {
    return "deactivated";
  }
  return "active";
}

export type AccountListItemProps = {
  account: AccountSummary;
  selected: boolean;
  onSelect: (accountId: string) => void;
};

export function AccountListItem({ account, selected, onSelect }: AccountListItemProps) {
  return (
    <button
      type="button"
      onClick={() => onSelect(account.accountId)}
      className={cn(
        "w-full rounded-lg border px-3 py-2 text-left transition-colors hover:bg-muted/40",
        selected && "border-primary bg-primary/5",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{account.email}</p>
          <p className="truncate text-xs text-muted-foreground">{account.planType}</p>
        </div>
        <StatusBadge status={normalizeStatus(account.status)} />
      </div>
    </button>
  );
}
