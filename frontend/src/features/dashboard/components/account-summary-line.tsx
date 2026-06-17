import type { AccountSummary } from "@/features/dashboard/schemas";
import { normalizeStatus } from "@/utils/account-status";

type AccountSummaryLineProps = {
  accounts: AccountSummary[];
};

export function AccountSummaryLine({ accounts }: AccountSummaryLineProps) {
  const registeredCount = accounts.length;
  const activeCount = accounts.filter((account) => normalizeStatus(account.status) === "active").length;
  const unavailableCount = registeredCount - activeCount;

  return (
    <div
      data-testid="dashboard-account-summary-line"
      className="flex items-center gap-1.5 whitespace-nowrap text-xs"
    >
      <span className="font-semibold tabular-nums text-foreground">{registeredCount}</span>
      <span className="text-muted-foreground">registered</span>
      <span className="text-border">·</span>
      <span className="font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">{activeCount}</span>
      <span className="text-muted-foreground">active</span>
      <span className="text-border">·</span>
      <span className="font-semibold tabular-nums text-red-600 dark:text-red-400">{unavailableCount}</span>
      <span className="text-muted-foreground">unavailable</span>
    </div>
  );
}
