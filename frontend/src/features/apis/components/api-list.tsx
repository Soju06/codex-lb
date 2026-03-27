import { Plus, Search } from "lucide-react";
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
import { ApiListItem } from "@/features/apis/components/api-list-item";
import type { ApiKey } from "@/features/api-keys/schemas";

const STATUS_FILTER_OPTIONS = ["all", "active", "disabled", "expired"];

export type ApiListProps = {
  apiKeys: ApiKey[];
  selectedKeyId: string | null;
  onSelect: (keyId: string) => void;
  onOpenCreate: () => void;
};

function isExpired(apiKey: ApiKey): boolean {
  if (!apiKey.expiresAt) return false;
  return new Date(apiKey.expiresAt).getTime() < Date.now();
}

function matchStatus(apiKey: ApiKey, filter: string): boolean {
  if (filter === "all") return true;
  const expired = isExpired(apiKey);
  if (filter === "active") return apiKey.isActive && !expired;
  if (filter === "disabled") return !apiKey.isActive;
  if (filter === "expired") return expired;
  return true;
}

export function ApiList({ apiKeys, selectedKeyId, onSelect, onOpenCreate }: ApiListProps) {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return apiKeys.filter((apiKey) => {
      if (!matchStatus(apiKey, statusFilter)) return false;
      if (!needle) return true;
      return (
        apiKey.name.toLowerCase().includes(needle) ||
        apiKey.keyPrefix.toLowerCase().includes(needle)
      );
    });
  }, [apiKeys, search, statusFilter]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/60" aria-hidden />
          <Input
            placeholder="Search API keys..."
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className="h-8 pl-8"
          />
        </div>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger size="sm" className="w-32 shrink-0">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            {STATUS_FILTER_OPTIONS.map((option) => (
              <SelectItem key={option} value={option}>
                {option === "all" ? "All statuses" : option.charAt(0).toUpperCase() + option.slice(1)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Button type="button" size="sm" onClick={onOpenCreate} className="h-8 w-full gap-1.5 text-xs">
        <Plus className="h-3.5 w-3.5" />
        Create API Key
      </Button>

      <div className="max-h-[calc(100vh-16rem)] space-y-1 overflow-y-auto p-1">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed p-6 text-center">
            <p className="text-sm font-medium text-muted-foreground">No matching API keys</p>
            <p className="text-xs text-muted-foreground/70">Try adjusting your filters.</p>
          </div>
        ) : (
          filtered.map((apiKey) => (
            <ApiListItem
              key={apiKey.id}
              apiKey={apiKey}
              selected={apiKey.id === selectedKeyId}
              onSelect={onSelect}
            />
          ))
        )}
      </div>
    </div>
  );
}
