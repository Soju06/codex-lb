import { Progress } from "@/components/ui/progress";
import type { AccountSummary } from "@/features/accounts/schemas";
import { formatPercent, formatQuotaResetLabel } from "@/utils/formatters";

export type AccountUsagePanelProps = {
  account: AccountSummary;
};

export function AccountUsagePanel({ account }: AccountUsagePanelProps) {
  const primary = account.usage?.primaryRemainingPercent ?? 0;
  const secondary = account.usage?.secondaryRemainingPercent ?? 0;

  return (
    <div className="space-y-3 rounded-lg border p-3">
      <h3 className="text-sm font-semibold">Usage</h3>

      <div className="space-y-1">
        <div className="flex items-center justify-between text-xs">
          <span>Primary remaining</span>
          <span>{formatPercent(primary)}</span>
        </div>
        <Progress value={primary} />
        <p className="text-xs text-muted-foreground">Reset {formatQuotaResetLabel(account.resetAtPrimary ?? null)}</p>
      </div>

      <div className="space-y-1">
        <div className="flex items-center justify-between text-xs">
          <span>Secondary remaining</span>
          <span>{formatPercent(secondary)}</span>
        </div>
        <Progress value={secondary} />
        <p className="text-xs text-muted-foreground">Reset {formatQuotaResetLabel(account.resetAtSecondary ?? null)}</p>
      </div>
    </div>
  );
}
