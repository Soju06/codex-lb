import { ChevronDown, ChevronUp, Plus, Upload } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { statusLabel } from "@/components/ui/status-glyph";
import { AccountFilterToolbar } from "@/features/accounts/components/account-filter-toolbar";
import { AccountListItem } from "@/features/accounts/components/account-list-item";
import { providerLabel } from "@/features/accounts/components/provider-label";
import { WindowsOauthHelp } from "@/features/accounts/components/windows-oauth-help";
import {
  filterAccounts,
  groupAccounts,
  hasActiveAccountFilters,
  medianWeeklyRemaining,
  type AccountFilterState,
  type AccountGroup,
  type AccountProviderFilter,
} from "@/features/accounts/filters";
import type {
  AccountProvider,
  AccountSummary,
} from "@/features/accounts/schemas";
import { sortAccountsForDisplay } from "@/features/accounts/sorting";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";

export type AccountListProps = {
  accounts: AccountSummary[];
  filters: AccountFilterState;
  onFiltersChange: (patch: Partial<AccountFilterState>) => void;
  onClearFilters: () => void;
  selectedAccountId: string | null;
  onSelect: (accountId: string) => void;
  onOpenImport: () => void;
  onOpenOauth: () => void;
};

function groupHeading(group: AccountGroup): string {
  const count = group.accounts.length;
  const noun = count === 1 ? "account" : "accounts";
  if (group.kind === "provider") {
    return `${providerLabel(group.id as AccountProvider)} — ${count} ${noun}`;
  }
  return `${statusLabel(group.id)} — ${count} ${noun}`;
}

function activeFilterSummary(filters: AccountFilterState): string {
  const parts: string[] = [];
  if (filters.provider !== "all") {
    parts.push(`provider ${providerLabel(filters.provider)}`);
  }
  if (filters.status !== "all") {
    parts.push(`status ${statusLabel(filters.status)}`);
  }
  if (filters.query.trim()) {
    parts.push(`search "${filters.query.trim()}"`);
  }
  return parts.join(", ");
}

export function AccountList({
  accounts,
  filters,
  onFiltersChange,
  onClearFilters,
  selectedAccountId,
  onSelect,
  onOpenImport,
  onOpenOauth,
}: AccountListProps) {
  const [helpOpen, setHelpOpen] = useState(false);
  const quotaDisplay = useAccountQuotaDisplayStore((s) => s.quotaDisplay);

  const sorted = useMemo(
    () => sortAccountsForDisplay(accounts, quotaDisplay, filters.sort),
    [accounts, quotaDisplay, filters.sort],
  );

  const filtered = useMemo(
    () => filterAccounts(sorted, filters),
    [sorted, filters],
  );

  // Provider counts reflect the other active filters (status + search) so
  // the segmented control always shows what each click would yield.
  const providerCounts = useMemo<Record<AccountProviderFilter, number>>(() => {
    const withoutProvider = filterAccounts(accounts, {
      ...filters,
      provider: "all",
    });
    const counts: Record<AccountProviderFilter, number> = {
      all: withoutProvider.length,
      openai: 0,
      anthropic: 0,
      glm: 0,
      kimi: 0,
      openrouter: 0,
    };
    for (const account of withoutProvider) {
      counts[account.provider ?? "openai"] += 1;
    }
    return counts;
  }, [accounts, filters]);

  const groups = useMemo(
    () => groupAccounts(filtered, filters.group),
    [filtered, filters.group],
  );

  const filtersActive = hasActiveAccountFilters(filters);

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onOpenImport}
          className="h-8 flex-1 gap-1.5 text-xs"
        >
          <Upload className="h-3.5 w-3.5" />
          Import
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={onOpenOauth}
          className="h-8 flex-1 gap-1.5 text-xs"
        >
          <Plus className="h-3.5 w-3.5" />
          Add Account
        </Button>
      </div>

      <AccountFilterToolbar
        filters={filters}
        providerCounts={providerCounts}
        onFiltersChange={onFiltersChange}
      />

      <div>
        <Button
          type="button"
          variant="link"
          size="sm"
          className="h-auto px-0 text-xs"
          onClick={() => setHelpOpen((current) => !current)}
        >
          Need help?
          {helpOpen ? (
            <ChevronUp className="h-3.5 w-3.5" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>

      {helpOpen ? <WindowsOauthHelp /> : null}

      <div className="max-h-[calc(100vh-18rem)] space-y-3 overflow-y-auto p-1">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-md border border-dashed p-6 text-center">
            {!filtersActive ? (
              <>
                <p className="text-sm font-medium text-muted-foreground">
                  No accounts yet
                </p>
                <p className="text-xs text-muted-foreground">
                  Add an account or import an auth file to get started.
                </p>
              </>
            ) : (
              <>
                <p className="text-sm font-medium text-muted-foreground">
                  No matching accounts
                </p>
                <p className="text-xs text-muted-foreground">
                  Active filters: {activeFilterSummary(filters)}.
                </p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  onClick={onClearFilters}
                >
                  Clear filters
                </Button>
              </>
            )}
          </div>
        ) : (
          groups.map((group) => {
            const median =
              group.kind === "none"
                ? null
                : medianWeeklyRemaining(group.accounts);
            return (
              <section
                key={group.id}
                className="space-y-1"
                aria-label={
                  group.kind === "provider"
                    ? `${providerLabel(group.id as AccountProvider)} accounts`
                    : group.kind === "status"
                      ? `${statusLabel(group.id)} accounts`
                      : "All accounts"
                }
              >
                {group.kind !== "none" ? (
                  <div className="sticky top-0 z-10 flex items-baseline justify-between gap-2 border-b bg-card/95 px-2 py-1.5 backdrop-blur">
                    <span className="truncate text-xs font-medium">
                      {groupHeading(group)}
                    </span>
                    {median !== null ? (
                      <span
                        className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums"
                        title="Median weekly window remaining"
                      >
                        wk mdn {Math.round(median)}%
                      </span>
                    ) : null}
                  </div>
                ) : null}
                <div className="space-y-1">
                  {group.accounts.map((account) => (
                    <AccountListItem
                      key={account.accountId}
                      account={account}
                      selected={account.accountId === selectedAccountId}
                      onSelect={onSelect}
                    />
                  ))}
                </div>
              </section>
            );
          })
        )}
      </div>
    </div>
  );
}
