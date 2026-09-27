import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronDown,
  ChevronUp,
  Plus,
  Search,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AccountListItem } from "@/features/accounts/components/account-list-item";
import { AccountListOverviewRow } from "@/features/accounts/components/account-list-overview-row";
import { AccountGridCard } from "@/features/accounts/components/account-grid-card";
import { cn } from "@/lib/utils";
import { AddAccountDialog } from "@/features/accounts/components/add-account-dialog";
import { WindowsOauthHelp } from "@/features/accounts/components/windows-oauth-help";
import type { AccountSummary } from "@/features/accounts/schemas";
import {
  ACCOUNT_SORT_OPTIONS,
  DEFAULT_ACCOUNT_SORT_MODE,
  sortAccountsForDisplay,
  type AccountSortColumn,
  type AccountSortMode,
} from "@/features/accounts/sorting";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { formatSlug } from "@/utils/formatters";

const STATUS_FILTER_OPTIONS = [
  "all",
  "active",
  "paused",
  "rate_limited",
  "quota_exceeded",
  "reauth_required",
  "deactivated",
];
const ACCOUNT_PAGE_SIZE = 24;

function AccountSortHeader({
  column,
  mode,
  onChange,
}: {
  column: AccountSortColumn;
  mode: AccountSortMode;
  onChange: (mode: AccountSortMode) => void;
}) {
  const { t } = useTranslation();
  const ascending = mode === `${column}_asc`;
  const descending = mode === `${column}_desc`;
  const active = ascending || descending;
  const Icon = ascending ? ArrowUp : descending ? ArrowDown : ArrowUpDown;
  const label = t(`accounts.sortColumn.${column}`);
  return (
    <button
      type="button"
      className={cn(
        "flex min-w-0 items-center gap-1 rounded text-left text-inherit uppercase hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active && "text-primary",
      )}
      aria-label={t("accounts.sortHeader", {
        label,
        direction: t(
          `accounts.sortDirection.${ascending ? "asc" : descending ? "desc" : "none"}`,
        ),
      })}
      aria-pressed={active}
      title={active ? t(`accounts.sort.${mode}`) : t("accounts.list.sortAria")}
      onClick={() => onChange(`${column}_${ascending ? "desc" : "asc"}`)}
    >
      <span className="truncate">{label}</span>
      <Icon className="size-3 shrink-0" aria-hidden="true" />
    </button>
  );
}

export type AccountViewMode = "detail" | "list" | "grid";

export type AccountListProps = {
  accounts: AccountSummary[];
  selectedAccountId: string | null;
  onSelect: (accountId: string) => void;
  onOpenImport: () => void;
  onOpenOauth: () => void;
  sortMode?: AccountSortMode;
  onSortModeChange?: (sortMode: AccountSortMode) => void;
  showResetCreditBadges?: boolean;
  readOnly?: boolean;
  viewMode?: AccountViewMode;
};

export function AccountList({
  accounts,
  selectedAccountId,
  onSelect,
  onOpenImport,
  onOpenOauth,
  sortMode,
  onSortModeChange,
  showResetCreditBadges = true,
  readOnly = false,
  viewMode = "list",
}: AccountListProps) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [helpOpen, setHelpOpen] = useState(false);
  const [chooserOpen, setChooserOpen] = useState(false);
  const [page, setPage] = useState(0);
  const [localSortMode, setLocalSortMode] = useState(DEFAULT_ACCOUNT_SORT_MODE);
  const quotaDisplay = useAccountQuotaDisplayStore((s) => s.quotaDisplay);
  const activeSortMode = sortMode ?? localSortMode;
  const changeSort = (nextMode: AccountSortMode) => {
    setLocalSortMode(nextMode);
    onSortModeChange?.(nextMode);
    setPage(0);
  };
  const sortHeader = (column: AccountSortColumn) => (
    <AccountSortHeader
      column={column}
      mode={activeSortMode}
      onChange={changeSort}
    />
  );

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return sortAccountsForDisplay(
      accounts,
      quotaDisplay,
      activeSortMode,
    ).filter((account) => {
      if (statusFilter !== "all" && account.status !== statusFilter) {
        return false;
      }
      if (!needle) {
        return true;
      }
      return (
        account.email.toLowerCase().includes(needle) ||
        (account.alias?.toLowerCase().includes(needle) ?? false) ||
        account.displayName.toLowerCase().includes(needle) ||
        account.accountId.toLowerCase().includes(needle) ||
        account.planType.toLowerCase().includes(needle)
      );
    });
  }, [accounts, quotaDisplay, search, statusFilter, activeSortMode]);
  const grid = viewMode === "grid";
  const detail = viewMode === "detail";
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(filtered.length / ACCOUNT_PAGE_SIZE) - 1),
  );
  const start = currentPage * ACCOUNT_PAGE_SIZE;
  const visible = detail
    ? filtered
    : filtered.slice(start, start + ACCOUNT_PAGE_SIZE);
  const Item = detail
    ? AccountListItem
    : grid
      ? AccountGridCard
      : AccountListOverviewRow;

  return (
    <div
      className={cn(
        "flex min-h-0 min-w-0 flex-1 flex-col space-y-3",
        detail && "max-h-[calc(100dvh-15rem)]",
      )}
    >
      <div
        className={cn(
          "grid grid-cols-1 gap-2 sm:grid-cols-2",
          !detail && "lg:grid-cols-4",
        )}
      >
        <div className="relative min-w-0 sm:col-span-2">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/60"
            aria-hidden
          />
          <Input
            placeholder={t("accounts.list.searchPlaceholder")}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(0);
            }}
            className="h-8 pl-8"
          />
        </div>
        <Select
          value={statusFilter}
          onValueChange={(value) => {
            setStatusFilter(value);
            setPage(0);
          }}
        >
          <SelectTrigger
            size="sm"
            className="w-full min-w-0"
            aria-label={t("accounts.list.statusFilterAria")}
          >
            <SelectValue placeholder={t("accounts.list.statusPlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            {STATUS_FILTER_OPTIONS.map((option) => (
              <SelectItem key={option} value={option}>
                {option === "all"
                  ? t("accounts.list.allStatuses")
                  : t(`accounts.statusFilters.${option}`, {
                      defaultValue: formatSlug(option),
                    })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={activeSortMode}
          onValueChange={(nextMode) => changeSort(nextMode as AccountSortMode)}
        >
          <SelectTrigger
            size="sm"
            className="w-full min-w-0"
            aria-label={t("accounts.list.sortAria")}
          >
            <SelectValue placeholder={t("accounts.list.sortPlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            {ACCOUNT_SORT_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {t(`accounts.sort.${option.value}`, {
                  defaultValue: option.label,
                })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button
          type="button"
          variant="link"
          size="sm"
          className="h-auto px-0 text-xs"
          onClick={() => setHelpOpen((current) => !current)}
        >
          {t("accounts.list.needHelp")}
          {helpOpen ? (
            <ChevronUp className="h-3.5 w-3.5" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" />
          )}
        </Button>
        <Button
          type="button"
          size="sm"
          className="gap-1.5"
          disabled={readOnly}
          onClick={() => setChooserOpen(true)}
        >
          <Plus className="h-3.5 w-3.5" />
          {t("accounts.list.addAccount")}
        </Button>
      </div>

      {helpOpen ? <WindowsOauthHelp /> : null}

      {viewMode === "list" && filtered.length > 0 ? (
        <div className="hidden grid-cols-[minmax(0,1.5fr)_minmax(0,.9fr)_minmax(0,.6fr)_minmax(0,1.5fr)] gap-5 rounded-lg bg-muted/50 px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground lg:grid">
          <span>{t("accounts.listOverview.identity")}</span>
          {sortHeader("plan")}
          {sortHeader("subscription")}
          <div className="grid min-w-0 grid-cols-2 gap-4">
            {sortHeader("quota_5h")}
            {sortHeader("quota_7d")}
          </div>
        </div>
      ) : null}

      <div
        className={cn(
          "min-h-0",
          detail
            ? "flex-1 space-y-1 overflow-y-auto p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
            : grid
              ? "grid grid-cols-1 items-stretch gap-4 md:grid-cols-2 xl:grid-cols-3"
              : "space-y-2",
        )}
        data-testid="account-list-scroll-region"
      >
        {filtered.length === 0 ? (
          <div className="col-span-full flex flex-col items-center gap-2 rounded-lg border border-dashed p-6 text-center">
            <p className="text-sm font-medium text-muted-foreground">
              {accounts.length === 0
                ? t("accounts.list.emptyTitle")
                : t("accounts.list.noMatches")}
            </p>
            <p className="text-xs text-muted-foreground/70">
              {accounts.length === 0
                ? t("accounts.list.emptyDescription")
                : t("accounts.list.adjustFilters")}
            </p>
          </div>
        ) : (
          visible.map((account) => (
            <Item
              key={account.accountId}
              account={account}
              {...(detail ? { showPlanRemaining: true } : {})}
              selected={account.accountId === selectedAccountId}
              showAccountId={account.isEmailDuplicate === true}
              showResetCreditBadge={showResetCreditBadges}
              onSelect={onSelect}
            />
          ))
        )}
      </div>

      {!detail && filtered.length > 0 ? (
        <nav
          aria-label={t("accounts.grid.pagination")}
          className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"
        >
          <span>
            {t("accounts.grid.range", {
              start: start + 1,
              end: Math.min(start + ACCOUNT_PAGE_SIZE, filtered.length),
              total: filtered.length,
            })}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage === 0}
              onClick={() => setPage(currentPage - 1)}
            >
              {t("accounts.grid.previous")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={start + ACCOUNT_PAGE_SIZE >= filtered.length}
              onClick={() => setPage(currentPage + 1)}
            >
              {t("accounts.grid.next")}
            </Button>
          </div>
        </nav>
      ) : null}

      {accounts.length > 0 ? (
        <p className="px-1 text-[11px] leading-snug text-muted-foreground/80">
          {t("accounts.list.statusEligibilityNote")}
        </p>
      ) : null}

      <AddAccountDialog
        open={chooserOpen}
        onOpenChange={setChooserOpen}
        onImport={onOpenImport}
        onAddAccount={onOpenOauth}
      />
    </div>
  );
}
