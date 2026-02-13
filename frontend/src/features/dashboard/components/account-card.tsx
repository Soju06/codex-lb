import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/status-badge";
import type { AccountSummary } from "@/features/dashboard/schemas";
import { formatPercent, formatQuotaResetLabel } from "@/utils/formatters";

type DashboardAccountStatus = "active" | "paused" | "limited" | "exceeded" | "deactivated";

type AccountAction = "details" | "resume" | "reauth";

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

export type AccountCardProps = {
  account: AccountSummary;
  onAction?: (account: AccountSummary, action: AccountAction) => void;
};

export function AccountCard({ account, onAction }: AccountCardProps) {
  const status = normalizeStatus(account.status);
  const primaryRemaining = account.usage?.primaryRemainingPercent ?? 0;
  const secondaryRemaining = account.usage?.secondaryRemainingPercent ?? 0;

  return (
    <Card className="gap-4 py-4">
      <CardHeader className="px-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="truncate text-sm">{account.displayName || account.email}</CardTitle>
            <p className="truncate text-xs text-muted-foreground">{account.accountId}</p>
          </div>
          <StatusBadge status={status} />
        </div>
      </CardHeader>
      <CardContent className="space-y-3 px-4 text-xs">
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span>Primary</span>
            <span className="text-muted-foreground">{formatPercent(primaryRemaining)}</span>
          </div>
          <Progress value={primaryRemaining} />
        </div>

        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span>Secondary</span>
            <span className="text-muted-foreground">{formatPercent(secondaryRemaining)}</span>
          </div>
          <Progress value={secondaryRemaining} />
        </div>

        <div className="flex items-center justify-between gap-3 text-muted-foreground">
          <span>Primary reset: {formatQuotaResetLabel(account.resetAtPrimary ?? null)}</span>
          <span>Secondary reset: {formatQuotaResetLabel(account.resetAtSecondary ?? null)}</span>
        </div>

        <div className="flex flex-wrap gap-2 pt-1">
          <Button type="button" size="sm" variant="outline" onClick={() => onAction?.(account, "details")}>
            Details
          </Button>
          {status === "paused" ? (
            <Button type="button" size="sm" variant="outline" onClick={() => onAction?.(account, "resume")}>
              Resume
            </Button>
          ) : null}
          {status === "deactivated" ? (
            <Button type="button" size="sm" variant="outline" onClick={() => onAction?.(account, "reauth")}>
              Re-authenticate
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
