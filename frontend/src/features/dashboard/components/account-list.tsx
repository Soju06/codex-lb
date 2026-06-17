import { ArrowDown, ArrowUp, ArrowUpDown, Clock, ExternalLink, List, Play, RotateCcw, Zap } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import type { AccountAction } from "@/features/dashboard/components/account-card";
import type { AccountSummary } from "@/features/dashboard/schemas";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { cn } from "@/lib/utils";
import { formatCompactAccountId } from "@/utils/account-identifiers";
import { normalizeStatus, quotaBarColor, quotaBarTrack } from "@/utils/account-status";
import { formatDateTimeInline, formatPercentNullable, formatQuotaResetLabel, formatSlug } from "@/utils/formatters";

const ACCOUNT_LIST_VISIBLE_ROWS = 8;
const ACCOUNT_LIST_ROW_HEIGHT_REM = 4.5;
const ACCOUNT_LIST_COLUMNS = "minmax(13rem,1.3fr) 7.75rem 5rem minmax(14rem,1.2fr) 6rem minmax(8rem,0.8fr) 6.5rem";

type AccountListProps = {
  accounts: AccountSummary[];
  readOnly?: boolean;
  onAction?: (account: AccountSummary, action: AccountAction) => void;
};

type AccountListSortKey = "account" | "status" | "plan" | "quota" | "credits" | "warmup";
type SortDirection = "asc" | "desc";
type AccountListSort = {
  key: AccountListSortKey;
  direction: SortDirection;
} | null;

const SORTABLE_HEADERS: Array<{ key: AccountListSortKey; label: string }> = [
  { key: "account", label: "Account" },
  { key: "status", label: "Status" },
  { key: "plan", label: "Plan" },
  { key: "quota", label: "Quota" },
  { key: "credits", label: "Credits" },
  { key: "warmup", label: "Warm-up" },
];

function quotaLabel(label: string, percent: number | null, resetAt: string | null | undefined) {
  return {
    label,
    percent,
    percentLabel: formatPercentNullable(percent),
    resetLabel: formatQuotaResetLabel(resetAt ?? null),
  };
}

function accountQuotaLabels(account: AccountSummary) {
  const weeklyOnly = account.windowMinutesPrimary == null && account.windowMinutesSecondary != null;
  const monthlyOnly =
    account.windowMinutesMonthly != null &&
    account.windowMinutesPrimary == null &&
    account.windowMinutesSecondary == null;

  if (monthlyOnly) {
    return [
      quotaLabel("Monthly", account.usage?.monthlyRemainingPercent ?? null, account.resetAtMonthly),
    ];
  }

  if (weeklyOnly) {
    return [
      quotaLabel("Weekly", account.usage?.secondaryRemainingPercent ?? null, account.resetAtSecondary),
    ];
  }

  return [
    quotaLabel("5h", account.usage?.primaryRemainingPercent ?? null, account.resetAtPrimary),
    quotaLabel("Weekly", account.usage?.secondaryRemainingPercent ?? null, account.resetAtSecondary),
  ];
}

function accountTitle(account: AccountSummary): string {
  return account.displayName || account.email || account.accountId;
}

function compareText(a: string, b: string): number {
  return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });
}

function accountQuotaSortValue(account: AccountSummary): number | null {
  const values = accountQuotaLabels(account)
    .map((quota) => quota.percent)
    .filter((percent): percent is number => percent !== null);
  if (values.length === 0) {
    return null;
  }
  return Math.min(...values);
}

function accountCreditsLabel(account: AccountSummary) {
  const monthlyOnly =
    account.windowMinutesMonthly != null &&
    account.windowMinutesPrimary == null &&
    account.windowMinutesSecondary == null;
  const weeklyOnly = account.windowMinutesPrimary == null && account.windowMinutesSecondary != null;
  const displayCredits = account.creditsBalance ?? (
    monthlyOnly
      ? account.remainingCreditsMonthly
      : weeklyOnly
        ? account.remainingCreditsSecondary
        : (account.remainingCreditsSecondary ?? account.remainingCreditsPrimary)
  );
  if (account.creditsUnlimited) {
    return "Unlimited";
  }
  return displayCredits === null || displayCredits === undefined ? "-" : displayCredits.toFixed(2);
}

function accountCreditsSortValue(account: AccountSummary): number | null {
  if (account.creditsUnlimited) {
    return Number.POSITIVE_INFINITY;
  }
  const value = Number(accountCreditsLabel(account));
  return Number.isFinite(value) ? value : null;
}

function compareNullableNumber(a: number | null, b: number | null, direction: SortDirection): number {
  if (a === null || b === null) {
    if (a === b) {
      return 0;
    }
    return a === null ? 1 : -1;
  }
  if (a === b) {
    return 0;
  }
  const result = a - b;
  return direction === "asc" ? result : -result;
}

function accountWarmupSortValue(account: AccountSummary): string {
  const enabledPrefix = account.limitWarmupEnabled ? "0" : "1";
  const attemptedAt = account.limitWarmup?.completedAt ?? account.limitWarmup?.attemptedAt ?? "";
  return `${enabledPrefix}|${attemptedAt}|${account.accountId}`;
}

function compareAccountsBySort(a: AccountSummary, b: AccountSummary, sort: AccountListSort): number {
  if (!sort) {
    return 0;
  }

  let result = 0;
  switch (sort.key) {
    case "account":
      result = compareText(accountTitle(a), accountTitle(b));
      break;
    case "status":
      result = compareText(normalizeStatus(a.status), normalizeStatus(b.status));
      break;
    case "plan":
      result = compareText(formatSlug(a.planType), formatSlug(b.planType));
      break;
    case "quota":
      result = compareNullableNumber(accountQuotaSortValue(a), accountQuotaSortValue(b), sort.direction);
      break;
    case "credits":
      result = compareNullableNumber(accountCreditsSortValue(a), accountCreditsSortValue(b), sort.direction);
      break;
    case "warmup":
      result = compareText(accountWarmupSortValue(a), accountWarmupSortValue(b));
      break;
  }

  if (result === 0) {
    result = compareText(accountTitle(a), accountTitle(b));
    return sort.direction === "asc" ? result : -result;
  }
  if (sort.key === "quota" || sort.key === "credits") {
    return result;
  }
  return sort.direction === "asc" ? result : -result;
}

function SortHeader({
  label,
  sortKey,
  activeSort,
  onSort,
}: {
  label: string;
  sortKey: AccountListSortKey;
  activeSort: AccountListSort;
  onSort: (key: AccountListSortKey) => void;
}) {
  const active = activeSort?.key === sortKey;
  const Icon = active ? (activeSort.direction === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
  const sortLabel = active ? `${label}, sorted ${activeSort.direction === "asc" ? "ascending" : "descending"}` : label;
  return (
    <button
      type="button"
      className={cn(
        "inline-flex min-w-0 items-center gap-1 text-left uppercase tracking-wider transition-colors hover:text-foreground",
        active ? "text-foreground" : "text-muted-foreground",
      )}
      aria-label={sortLabel}
      onClick={() => onSort(sortKey)}
    >
      <span className="truncate">{label}</span>
      <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
    </button>
  );
}

function AccountQuotaCells({ account }: { account: AccountSummary }) {
  return (
    <div className="grid gap-1.5 text-xs">
      {accountQuotaLabels(account).map((quota) => (
        <div key={quota.label} className="grid grid-cols-[2.75rem_minmax(3rem,auto)_minmax(2.75rem,0.45fr)_minmax(0,1fr)] items-center gap-2">
          <span className="text-muted-foreground">{quota.label}</span>
          <span className="font-medium tabular-nums text-foreground">{quota.percentLabel}</span>
          <QuotaMeter percent={quota.percent} />
          <span className="inline-flex min-w-0 items-center gap-1 text-[11px] text-muted-foreground">
            <Clock className="h-3 w-3 shrink-0" aria-hidden="true" />
            <span className="truncate">{quota.resetLabel}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

function QuotaMeter({ percent }: { percent: number | null }) {
  const clamped = percent === null ? 0 : Math.max(0, Math.min(100, percent));
  return (
    <div
      className={cn("h-1.5 overflow-hidden rounded-full", quotaBarTrack(clamped))}
      aria-hidden="true"
      data-testid="account-list-quota-meter"
    >
      <div
        className={cn("h-full rounded-full transition-all duration-500 ease-out", quotaBarColor(clamped))}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

export function AccountList({ accounts, readOnly = false, onAction }: AccountListProps) {
  const blurred = usePrivacyStore((s) => s.blurred);
  const [sort, setSort] = useState<AccountListSort>(null);
  const sortedAccounts = useMemo(() => {
    if (!sort) {
      return accounts;
    }
    return accounts
      .map((account, index) => ({ account, index }))
      .sort((a, b) => compareAccountsBySort(a.account, b.account, sort) || a.index - b.index)
      .map((entry) => entry.account);
  }, [accounts, sort]);

  const handleSort = (key: AccountListSortKey) => {
    setSort((current) => {
      if (current?.key !== key) {
        return { key, direction: "asc" };
      }
      return { key, direction: current.direction === "asc" ? "desc" : "asc" };
    });
  };

  if (accounts.length === 0) {
    return (
      <EmptyState
        icon={List}
        title="No accounts connected yet"
        description="Import or authenticate an account to get started."
      />
    );
  }

  return (
    <div
      data-testid="dashboard-account-list"
      className="overflow-x-auto rounded-lg border bg-card"
    >
      <div
        className="min-w-[54rem] divide-y overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        style={{ maxHeight: `${ACCOUNT_LIST_VISIBLE_ROWS * ACCOUNT_LIST_ROW_HEIGHT_REM}rem` }}
      >
        <div
          className="sticky top-0 z-10 grid gap-3 border-b bg-card/95 px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground backdrop-blur supports-[backdrop-filter]:bg-card/85"
          style={{ gridTemplateColumns: ACCOUNT_LIST_COLUMNS }}
        >
          {SORTABLE_HEADERS.map((header) => (
            <SortHeader
              key={header.key}
              label={header.label}
              sortKey={header.key}
              activeSort={sort}
              onSort={handleSort}
            />
          ))}
          <span className="text-right">Actions</span>
        </div>
        {sortedAccounts.map((account, index) => {
          const status = normalizeStatus(account.status);
          const title = accountTitle(account);
          const emailSubtitle =
            account.displayName && account.displayName !== account.email
              ? account.email
              : null;
          const compactId = formatCompactAccountId(account.accountId);
          const showAccountId = account.isEmailDuplicate === true;
          const warmupDetail = account.limitWarmup
            ? `${formatSlug(account.limitWarmup.status)} | ${account.limitWarmup.window === "primary" ? "5h" : "weekly"} | ${formatDateTimeInline(account.limitWarmup.completedAt ?? account.limitWarmup.attemptedAt)}`
            : "No attempts";
          return (
            <div
              key={account.accountId}
              data-testid="account-list-row"
              className="grid min-h-[4.5rem] items-center gap-3 px-3 py-2 text-sm"
              style={{ animationDelay: `${index * 50}ms`, gridTemplateColumns: ACCOUNT_LIST_COLUMNS }}
            >
              <div className="min-w-0">
                <p className="truncate font-medium leading-tight">
                  <span className={blurred ? "privacy-blur" : undefined}>{title}</span>
                </p>
                <p className="mt-1 truncate text-xs text-muted-foreground">
                  {emailSubtitle ? (
                    <span className={blurred ? "privacy-blur" : undefined}>{emailSubtitle}</span>
                  ) : (
                    `ID ${compactId}`
                  )}
                  {showAccountId && emailSubtitle ? ` | ID ${compactId}` : ""}
                </p>
              </div>
              <StatusBadge status={status} />
              <span className="text-xs text-muted-foreground">{formatSlug(account.planType)}</span>
              <AccountQuotaCells account={account} />
              <span className="font-medium tabular-nums">{accountCreditsLabel(account)}</span>
              <div className="min-w-0 text-xs">
                <p className={cn("font-medium", account.limitWarmupEnabled ? "text-primary" : "text-muted-foreground")}>
                  {account.limitWarmupEnabled ? "On" : "Off"}
                </p>
                <p className="truncate text-[11px] text-muted-foreground">{warmupDetail}</p>
              </div>
              <div className="flex justify-end gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 w-7 rounded-md p-0 text-muted-foreground hover:text-foreground"
                  aria-label={`View details for ${title}`}
                  title="Details"
                  onClick={() => onAction?.(account, "details")}
                >
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className={cn(
                    "h-7 w-7 rounded-md p-0",
                    account.limitWarmupEnabled
                      ? "text-primary hover:bg-primary/10 hover:text-primary"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  aria-label={`${account.limitWarmupEnabled ? "Disable" : "Enable"} limit warm-up for ${title}`}
                  title="Limit warm-up"
                  disabled={readOnly}
                  onClick={() => onAction?.(account, "warmup-toggle")}
                >
                  <Zap className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
                {status === "paused" ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-7 w-7 rounded-md p-0 text-emerald-600 hover:bg-emerald-500/10 hover:text-emerald-700 dark:text-emerald-400 dark:hover:text-emerald-300"
                    aria-label={`Resume ${title}`}
                    title="Resume"
                    disabled={readOnly}
                    onClick={() => onAction?.(account, "resume")}
                  >
                    <Play className="h-3.5 w-3.5" aria-hidden="true" />
                  </Button>
                ) : null}
                {status === "reauth" || status === "deactivated" ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-7 w-7 rounded-md p-0 text-amber-600 hover:bg-amber-500/10 hover:text-amber-700 dark:text-amber-400 dark:hover:text-amber-300"
                    aria-label={`Re-authenticate ${title}`}
                    title="Re-authenticate"
                    disabled={readOnly}
                    onClick={() => onAction?.(account, "reauth")}
                  >
                    <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                  </Button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
