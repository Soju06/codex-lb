import { Flame, Shield } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { isEmailLabel } from "@/components/blur-email";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { StatusBadge } from "@/components/status-badge";
import type { AccountRoutingPolicy, AccountSummary } from "@/features/accounts/schemas";
import { normalizeStatus, quotaBarColor, quotaBarTrack } from "@/utils/account-status";
import { formatCompactAccountId } from "@/utils/account-identifiers";
import { formatPercentNullable, formatQuotaResetLabel, formatSlug } from "@/utils/formatters";

export type AccountListItemProps = {
  account: AccountSummary;
  selected: boolean;
  showAccountId?: boolean;
  onSelect: (accountId: string) => void;
};

function MiniQuotaBar({ percent, testId }: { percent: number | null; testId: string }) {
  if (percent === null) {
    return <div data-testid={testId} className="h-1 flex-1 overflow-hidden rounded-full bg-muted" />;
  }
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div data-testid={testId} className={cn("h-1 flex-1 overflow-hidden rounded-full", quotaBarTrack(clamped))}>
      <div
        data-testid={`${testId}-fill`}
        className={cn("h-full rounded-full", quotaBarColor(clamped))}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

function RoutingPolicyBadge({ policy }: { policy: AccountRoutingPolicy | undefined }) {
  if (policy === "burn_first") {
    return (
      <Badge variant="outline" className="shrink-0 gap-1 border-amber-300 bg-amber-50 px-1.5 text-[11px] text-amber-700">
        <Flame className="h-3 w-3" aria-hidden="true" />
        Burn first
      </Badge>
    );
  }
  if (policy === "preserve") {
    return (
      <Badge variant="outline" className="shrink-0 gap-1 border-sky-300 bg-sky-50 px-1.5 text-[11px] text-sky-700">
        <Shield className="h-3 w-3" aria-hidden="true" />
        Preserve
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="shrink-0 px-1.5 text-[11px] text-muted-foreground">
      Normal
    </Badge>
  );
}

export function AccountListItem({ account, selected, showAccountId = false, onSelect }: AccountListItemProps) {
  const blurred = usePrivacyStore((s) => s.blurred);
  const quotaDisplay = useAccountQuotaDisplayStore((s) => s.quotaDisplay);
  const status = normalizeStatus(account.status);
  const title = account.displayName || account.email;
  const titleIsEmail = isEmailLabel(title, account.email);
  const emailSubtitle = account.displayName && account.displayName !== account.email
    ? account.email
    : null;
  const baseSubtitle = emailSubtitle ?? formatSlug(account.planType);
  const idSuffix = showAccountId ? ` | ID ${formatCompactAccountId(account.accountId)}` : "";
  const primary = account.usage?.primaryRemainingPercent ?? null;
  const secondary = account.usage?.secondaryRemainingPercent ?? null;
  const hasPrimaryWindow = account.windowMinutesPrimary != null || primary !== null || account.resetAtPrimary != null;
  const hasSecondaryWindow = account.windowMinutesSecondary != null || secondary !== null || account.resetAtSecondary != null;
  const showPrimaryRow = hasPrimaryWindow && (quotaDisplay !== "weekly" || !hasSecondaryWindow);
  const showSecondaryRow = hasSecondaryWindow && (quotaDisplay !== "5h" || !hasPrimaryWindow);
  const visibleQuotaRows = Number(showPrimaryRow) + Number(showSecondaryRow);

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
            {emailSubtitle ? <><span className={blurred ? "privacy-blur" : undefined}>{emailSubtitle}</span>{idSuffix}</> : <>{baseSubtitle}{idSuffix}</>}
          </p>
        </div>
        <StatusBadge status={status} />
        <RoutingPolicyBadge policy={account.routingPolicy} />
      </div>
      <div className={cn("mt-2 grid gap-2", visibleQuotaRows > 1 ? "grid-cols-2" : "grid-cols-1")}>
        {showPrimaryRow ? <MiniQuotaRow label="5h" percent={primary} resetAt={account.resetAtPrimary} /> : null}
        {showSecondaryRow ? <MiniQuotaRow label="Weekly" percent={secondary} resetAt={account.resetAtSecondary} /> : null}
      </div>
    </button>
  );
}

function MiniQuotaRow({
  label,
  percent,
  resetAt,
}: {
  label: string;
  percent: number | null;
  resetAt: string | null | undefined;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[11px]">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums font-medium">{formatPercentNullable(percent)}</span>
      </div>
      <MiniQuotaBar percent={percent} testId={`mini-quota-track-${label.toLowerCase()}`} />
      <div className="text-[10px] text-muted-foreground">{formatMiniQuotaResetLabel(resetAt ?? null)}</div>
    </div>
  );
}

function formatMiniQuotaResetLabel(resetAt: string | null): string {
  const label = formatQuotaResetLabel(resetAt);
  return label.startsWith("Reset ") ? label : `Reset ${label}`;
}
