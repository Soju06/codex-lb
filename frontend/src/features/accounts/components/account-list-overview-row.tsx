import { ArrowUpRight, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import { isEmailLabel } from "@/components/blur-email";
import { MiniQuotaBar } from "@/components/mini-quota-bar";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { AccountSubscription } from "@/features/accounts/components/account-subscription";
import type { AccountSummary } from "@/features/accounts/schemas";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { useSmoothPercent } from "@/hooks/use-smooth-percent";
import { cn } from "@/lib/utils";
import { normalizeStatus } from "@/utils/account-status";
import { formatCompactAccountId } from "@/utils/account-identifiers";
import {
  formatPercentNullable,
  formatQuotaResetLabel,
  formatSlug,
} from "@/utils/formatters";

type AccountListOverviewRowProps = {
  account: AccountSummary;
  selected: boolean;
  showAccountId?: boolean;
  showResetCreditBadge?: boolean;
  onSelect: (accountId: string) => void;
};

/** Compact overview with full metadata and management available in Detail view. */
export function AccountListOverviewRow({
  account,
  selected,
  showAccountId = false,
  showResetCreditBadge = true,
  onSelect,
}: AccountListOverviewRowProps) {
  const { t } = useTranslation();
  const blurred = usePrivacyStore((s) => s.blurred);
  const quotaDisplay = useAccountQuotaDisplayStore((s) => s.quotaDisplay);
  const status = normalizeStatus(account.status);
  const primary = useSmoothPercent(
    account.usage?.primaryRemainingPercent ?? null,
  );
  const secondary = useSmoothPercent(
    account.usage?.secondaryRemainingPercent ?? null,
  );
  const monthly = useSmoothPercent(
    account.usage?.monthlyRemainingPercent ?? null,
  );
  const hasPrimary =
    account.windowMinutesPrimary != null ||
    primary.everKnown ||
    account.resetAtPrimary != null;
  const hasSecondary =
    account.windowMinutesSecondary != null ||
    secondary.everKnown ||
    account.resetAtSecondary != null;
  const hasMonthly =
    account.windowMinutesMonthly != null ||
    monthly.everKnown ||
    account.resetAtMonthly != null;
  const monthlyOnly = hasMonthly && !hasPrimary && !hasSecondary;
  const showPrimary =
    !monthlyOnly && hasPrimary && (quotaDisplay !== "weekly" || !hasSecondary);
  const showSecondary =
    !monthlyOnly && hasSecondary && (quotaDisplay !== "5h" || !hasPrimary);
  const label = account.alias?.trim() || account.displayName || account.email;
  const workspace =
    account.workspaceLabel ||
    account.workspaceId ||
    account.chatgptAccountId ||
    t("accounts.detail.unknownWorkspace");

  return (
    <button
      type="button"
      data-testid="account-list-overview-row"
      aria-haspopup="dialog"
      onClick={() => onSelect(account.accountId)}
      className={cn(
        "group grid w-full min-w-0 grid-cols-2 items-center gap-x-4 gap-y-2 rounded-lg border bg-card px-4 py-2.5 text-left transition-colors hover:border-primary/30 hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "lg:grid-cols-[minmax(0,1.5fr)_minmax(0,.9fr)_minmax(0,.6fr)_minmax(0,1.5fr)] lg:gap-5",
        selected && "border-primary/30 bg-primary/[0.03]",
      )}
    >
      <div className="col-span-2 min-w-0 lg:col-span-1">
        <div className="flex min-w-0 items-center gap-2">
          <div className="min-w-0 flex-1">
            <p
              className={cn(
                "truncate text-sm font-semibold",
                blurred &&
                  !account.alias &&
                  isEmailLabel(label, account.email) &&
                  "privacy-blur",
              )}
            >
              {label}
            </p>
          </div>
          {account.securityWorkAuthorized ? (
            <ShieldCheck
              className="size-4 shrink-0 text-emerald-600"
              aria-label={t("accounts.actions.trustedAccess")}
            />
          ) : null}
          <ArrowUpRight
            className="size-3.5 shrink-0 text-muted-foreground group-hover:text-primary"
            aria-label={t("accounts.grid.details")}
          />
        </div>
        <p
          className="mt-1 truncate text-[11px] text-muted-foreground"
          title={
            showAccountId
              ? t("accounts.detail.accountIdTitle", {
                  accountId: account.accountId,
                })
              : workspace
          }
        >
          {label !== account.email ? (
            <>
              <span className={blurred ? "privacy-blur" : undefined}>
                {account.email}
              </span>
              {" · "}
            </>
          ) : null}
          <span>
            {workspace}
            {account.seatType ? ` · ${formatSlug(account.seatType)}` : ""}
          </span>
          {showAccountId
            ? ` | ID ${formatCompactAccountId(account.accountId)}`
            : ""}
        </p>
      </div>

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="secondary" className="text-[10px]">
            {formatSlug(account.planType)}
          </Badge>
          <StatusBadge
            status={status}
            title={
              account.deactivationReason ||
              (status === "active"
                ? t("accounts.listItem.statusActiveHint")
                : undefined)
            }
          />
          {showResetCreditBadge && (account.availableResetCredits ?? 0) > 0 ? (
            <Badge
              variant="outline"
              className="border-primary/20 bg-primary/5 text-[10px] tabular-nums text-primary"
              title={t("accounts.resetCreditDialog.availableCount", {
                count: account.availableResetCredits ?? 0,
              })}
            >
              {t("accounts.actions.resetWithCount", {
                count: account.availableResetCredits ?? 0,
              })}
            </Badge>
          ) : null}
        </div>
      </div>

      <div className="min-w-0 text-right lg:text-left">
        <span className="mr-1 text-[10px] text-muted-foreground lg:hidden">
          {t("accounts.listOverview.subscription")}
        </span>
        <AccountSubscription account={account} compact />
      </div>

      <div
        className={cn(
          "col-span-2 grid min-w-0 gap-4 lg:col-span-1",
          showPrimary && showSecondary ? "grid-cols-2" : "grid-cols-1",
        )}
      >
        {monthlyOnly ? (
          <QuotaCell
            label={t("common.quota.monthly")}
            percent={monthly.percent}
            resetAt={account.resetAtMonthly}
          />
        ) : null}
        {showPrimary ? (
          <QuotaCell
            label="5h"
            percent={primary.percent}
            resetAt={account.resetAtPrimary}
          />
        ) : null}
        {showSecondary ? (
          <QuotaCell
            label={t("common.quota.weekly")}
            percent={secondary.percent}
            resetAt={account.resetAtSecondary}
          />
        ) : null}
        {!monthlyOnly && !showPrimary && !showSecondary ? (
          <p className="text-xs text-muted-foreground">—</p>
        ) : null}
      </div>
    </button>
  );
}

function QuotaCell({
  label,
  percent,
  resetAt,
}: {
  label: string;
  percent: number | null;
  resetAt: string | null | undefined;
}) {
  const { t } = useTranslation();
  const reset = formatQuotaResetLabel(resetAt ?? null);
  return (
    <div className="min-w-0 space-y-1">
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="min-w-0 truncate" title={label}>
          {label}
        </span>
        <span className="shrink-0 font-medium tabular-nums">
          {formatPercentNullable(percent, 1)}
        </span>
      </div>
      <MiniQuotaBar
        aria-label={t("accounts.listItem.quotaRemainingAria", { label })}
        percent={percent}
        testId={`list-overview-quota-${label}`}
      />
      <p className="truncate text-[10px] text-muted-foreground" title={reset}>
        {reset.startsWith("Reset ")
          ? reset
          : t("accounts.listItem.resetAt", { label: reset })}
      </p>
    </div>
  );
}
