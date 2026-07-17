import { Flame, Shield, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { isEmailLabel } from "@/components/blur-email";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { StatusBadge } from "@/components/status-badge";
import { MiniQuotaBar } from "@/components/mini-quota-bar";
import type {
  AccountRoutingPolicy,
  AccountSummary,
} from "@/features/accounts/schemas";
import { normalizeStatus } from "@/utils/account-status";
import { formatCompactAccountId } from "@/utils/account-identifiers";
import {
  formatDateTimeInline,
  formatPercentNullable,
  formatQuotaResetLabel,
  formatSlug,
} from "@/utils/formatters";

export type AccountListItemProps = {
  account: AccountSummary;
  selected: boolean;
  showAccountId?: boolean;
  showResetCreditBadge?: boolean;
  onSelect: (accountId: string) => void;
};

export function AccountListItem({
  account,
  selected,
  showAccountId = false,
  showResetCreditBadge = true,
  onSelect,
}: AccountListItemProps) {
  const { t } = useTranslation();
  const blurred = usePrivacyStore((s) => s.blurred);
  const quotaDisplay = useAccountQuotaDisplayStore((s) => s.quotaDisplay);
  const status = normalizeStatus(account.status);
  const title = account.displayName || account.email;
  const titleIsEmail = isEmailLabel(title, account.email);
  const emailSubtitle = account.displayName && account.displayName !== account.email
    ? account.email
    : null;
  const workspaceLabel = account.chatgptAccountId || account.workspaceLabel || account.workspaceId || t("accounts.detail.unknownWorkspace");
  const seatLabel = account.seatType ? ` | ${formatSlug(account.seatType)}` : "";
  const slotSubtitle = `${formatSlug(account.planType)} | ${workspaceLabel}${seatLabel}`;
  const idSuffix = showAccountId ? ` | ID ${formatCompactAccountId(account.accountId)}` : "";
  const primary = account.usage?.primaryRemainingPercent ?? null;
  const secondary = account.usage?.secondaryRemainingPercent ?? null;
  const monthly = account.usage?.monthlyRemainingPercent ?? null;
  const hasPrimaryWindow =
    account.windowMinutesPrimary != null ||
    primary !== null ||
    account.resetAtPrimary != null;
  const hasSecondaryWindow =
    account.windowMinutesSecondary != null ||
    secondary !== null ||
    account.resetAtSecondary != null;
  const hasMonthlyWindow =
    account.windowMinutesMonthly != null ||
    monthly !== null ||
    account.resetAtMonthly != null;
  const monthlyOnly = hasMonthlyWindow && !hasPrimaryWindow && !hasSecondaryWindow;
  const showMonthlyRow = monthlyOnly;
  const showPrimaryRow =
    !monthlyOnly && hasPrimaryWindow && (quotaDisplay !== "weekly" || !hasSecondaryWindow);
  const showSecondaryRow =
    !monthlyOnly && hasSecondaryWindow && (quotaDisplay !== "5h" || !hasPrimaryWindow);
  const visibleQuotaRows = Number(showPrimaryRow) + Number(showSecondaryRow) + Number(showMonthlyRow);
  const showRoutingPolicy = status !== "reauth" && status !== "deactivated";
  const warmupLabel = account.limitWarmupEnabled ? t("accounts.listItem.warmupOn") : t("accounts.listItem.warmupOff");
  const warmupMeta = account.limitWarmup
    ? `${formatSlug(account.limitWarmup.status)} | ${formatSlug(account.limitWarmup.model)} | ${formatDateTimeInline(account.limitWarmup.completedAt ?? account.limitWarmup.attemptedAt)}`
    : t("accounts.listItem.noAttempts");
  const availableResetCredits = account.availableResetCredits ?? 0;
  const resetBadgeLabel = availableResetCredits > 99 ? "99+" : String(availableResetCredits);

  return (
    <button
      type="button"
      onClick={() => onSelect(account.accountId)}
      className={cn(
        "relative min-w-0 w-full rounded-lg px-3 py-2.5 text-left transition-colors",
        selected ? "bg-primary/8 ring-1 ring-primary/25" : "hover:bg-muted/50",
      )}
    >
      {showResetCreditBadge && availableResetCredits > 0 ? (
        <span className="absolute -top-1 -right-1 grid h-5 min-w-[1.25rem] place-items-center rounded-full bg-primary px-1 text-[10px] font-medium text-primary-foreground">
          {resetBadgeLabel}
        </span>
      ) : null}
      <div className="flex items-start gap-2.5">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">
            {titleIsEmail && blurred ? (
              <span className="privacy-blur">{title}</span>
            ) : (
              title
            )}
          </p>
          <p className="truncate text-xs text-muted-foreground" title={showAccountId ? t("accounts.detail.accountIdTitle", { accountId: account.accountId }) : undefined}>
            {emailSubtitle ? <><span className={blurred ? "privacy-blur" : undefined}>{emailSubtitle}</span> | {slotSubtitle}{idSuffix}</> : <>{slotSubtitle}{idSuffix}</>}
          </p>
        </div>
        {showRoutingPolicy ? (
          <RoutingPolicyBadge
            policy={account.routingPolicy as AccountRoutingPolicy | undefined}
          />
        ) : null}
        {account.securityWorkAuthorized === true ? (
          <ShieldCheck
            className="h-3.5 w-3.5 text-emerald-600"
            aria-label={t("accounts.actions.trustedAccess")}
          />
        ) : null}
        <StatusBadge status={status} />
      </div>
      <div
        className={cn(
          "mt-2 grid gap-2",
          visibleQuotaRows > 1 ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1",
        )}
      >
        {showMonthlyRow ? (
          <MiniQuotaRow
            label={t("common.quota.monthly")}
            percent={monthly}
            resetAt={account.resetAtMonthly}
          />
        ) : null}
        {showPrimaryRow ? (
          <MiniQuotaRow
            label="5h"
            percent={primary}
            resetAt={account.resetAtPrimary}
          />
        ) : null}
        {showSecondaryRow ? (
          <MiniQuotaRow
            label={t("common.quota.weekly")}
            percent={secondary}
            resetAt={account.resetAtSecondary}
          />
        ) : null}
      </div>
      <div className="mt-2 flex min-w-0 items-center justify-between gap-2 text-[10px] text-muted-foreground">
        <span className="shrink-0">{warmupLabel}</span>
        <span className="min-w-0 truncate">{warmupMeta}</span>
      </div>
    </button>
  );
}

function RoutingPolicyBadge({
  policy,
}: {
  policy: AccountRoutingPolicy | undefined;
}) {
  const { t } = useTranslation();
  if (policy === "burn_first") {
    return (
      <Badge
        variant="outline"
        className="shrink-0 gap-1 border-amber-300 bg-amber-50 px-1.5 text-[11px] text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300"
      >
        <Flame className="h-3 w-3" aria-hidden="true" />
        {t("common.routingPolicies.burnFirst")}
      </Badge>
    );
  }
  if (policy === "preserve") {
    return (
      <Badge
        variant="outline"
        className="shrink-0 gap-1 border-sky-300 bg-sky-50 px-1.5 text-[11px] text-sky-700 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-300"
      >
        <Shield className="h-3 w-3" aria-hidden="true" />
        {t("common.routingPolicies.preserve")}
      </Badge>
    );
  }
  return (
    <Badge
      variant="outline"
      className="shrink-0 px-1.5 text-[11px] text-muted-foreground"
    >
      {t("common.routingPolicies.normal")}
    </Badge>
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
  const { t } = useTranslation();
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[11px]">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums font-medium">
          {formatPercentNullable(percent)}
        </span>
      </div>
      <MiniQuotaBar
        aria-label={t("accounts.listItem.quotaRemainingAria", { label })}
        percent={percent}
        testId={`mini-quota-track-${label.toLowerCase()}`}
      />
      <div className="text-[10px] text-muted-foreground">
        {formatMiniQuotaResetLabel(resetAt ?? null, t)}
      </div>
    </div>
  );
}

function formatMiniQuotaResetLabel(resetAt: string | null, t: ReturnType<typeof useTranslation>["t"]): string {
  const label = formatQuotaResetLabel(resetAt);
  return label.startsWith("Reset ") ? label : t("accounts.listItem.resetAt", { label });
}
