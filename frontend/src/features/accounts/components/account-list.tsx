import { useMemo, useState } from "react";

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
import type { AccountSummary } from "@/features/accounts/schemas";

const STATUS_FILTER_OPTIONS = ["all", "active", "paused", "rate_limited", "quota_exceeded", "deactivated"];

export type AccountListProps = {
  accounts: AccountSummary[];
  selectedAccountId: string | null;
  onSelect: (accountId: string) => void;
  onOpenImport: () => void;
  onOpenOauth: () => void;
};

export function AccountList({
  accounts,
  selectedAccountId,
  onSelect,
  onOpenImport,
  onOpenOauth,
}: AccountListProps) {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return accounts.filter((account) => {
      if (statusFilter !== "all" && account.status !== statusFilter) {
        return false;
      }
      if (!needle) {
        return true;
      }
      return (
        account.email.toLowerCase().includes(needle) ||
        account.accountId.toLowerCase().includes(needle) ||
        account.planType.toLowerCase().includes(needle)
      );
    });
  }, [accounts, search, statusFilter]);

  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <Input
          placeholder="Search accounts"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="h-8"
        />
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger size="sm">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            {STATUS_FILTER_OPTIONS.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex gap-2">
        <Button type="button" size="sm" variant="outline" onClick={onOpenImport} className="flex-1">
          Import
        </Button>
        <Button type="button" size="sm" onClick={onOpenOauth} className="flex-1">
          Add Account
        </Button>
      </div>

      <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
        {filtered.length === 0 ? (
          <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">No matching accounts.</p>
        ) : (
          filtered.map((account) => (
            <AccountListItem
              key={account.accountId}
              account={account}
              selected={account.accountId === selectedAccountId}
              onSelect={onSelect}
            />
          ))
        )}
      </div>
    </div>
  );
}
