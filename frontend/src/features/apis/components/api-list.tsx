import { ArrowDown, ArrowUp, ArrowUpDown, Plus, Search } from "lucide-react";
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
import { ApiListItem } from "@/features/apis/components/api-list-item";
import { ApiListOverviewRow } from "./api-list-overview-row";
import {
  API_LIST_COLUMNS,
  API_SORT_COLUMNS,
  sortApiKeys,
  type ApiSortColumn,
  type ApiSortMode,
  type ApiViewMode,
} from "@/features/apis/list-utils";
import { hasApiKeyUsage } from "@/features/api-keys/usage";
import type { ApiKey } from "@/features/api-keys/schemas";
import { cn } from "@/lib/utils";

const STATUS_FILTER_OPTIONS = ["all", "active", "disabled", "expired"] as const;
const STATUS_FILTER_LABEL_KEYS = {
  active: "common.states.active",
  disabled: "common.states.disabled",
  expired: "common.states.expired",
} as const;

export type ApiListProps = {
  apiKeys: ApiKey[];
  selectedKeyId: string | null;
  onSelect: (keyId: string) => void;
  onOpenCreate: () => void;
  viewMode?: ApiViewMode;
  readOnly?: boolean;
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

export function ApiList({
  apiKeys,
  selectedKeyId,
  onSelect,
  onOpenCreate,
  viewMode = "detail",
  readOnly = false,
}: ApiListProps) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [usageFilter, setUsageFilter] = useState("all");
  const [sortMode, setSortMode] = useState<ApiSortMode>("default");
  const [page, setPage] = useState(0);
  const detail = viewMode === "detail";

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return apiKeys.filter((apiKey) => {
      if (!matchStatus(apiKey, statusFilter)) return false;
      if (
        usageFilter !== "all" &&
        hasApiKeyUsage(apiKey) !== (usageFilter === "used")
      )
        return false;
      if (!needle) return true;
      return (
        apiKey.name.toLowerCase().includes(needle) ||
        apiKey.keyPrefix.toLowerCase().includes(needle)
      );
    });
  }, [apiKeys, search, statusFilter, usageFilter]);
  const sorted = useMemo(
    () => sortApiKeys(filtered, sortMode),
    [filtered, sortMode],
  );
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(sorted.length / 24) - 1),
  );
  const start = currentPage * 24;
  const visible = detail ? sorted : sorted.slice(start, start + 24);
  const changeSort = (mode: ApiSortMode) => {
    setSortMode(mode);
    setPage(0);
  };
  const sortHeader = (column: ApiSortColumn) => {
    const asc = sortMode === `${column}_asc`;
    const desc = sortMode === `${column}_desc`;
    const Icon = asc ? ArrowUp : desc ? ArrowDown : ArrowUpDown;
    const label = t(`apis.list.column.${column}`);
    const direction = t(
      asc
        ? "accounts.sortDirection.asc"
        : desc
          ? "accounts.sortDirection.desc"
          : "accounts.sortDirection.none",
    );
    return (
      <button
        key={column}
        type="button"
        className="flex min-w-0 items-center gap-1 text-left"
        aria-label={`${label}: ${direction}`}
        aria-pressed={asc || desc}
        onClick={() => changeSort(`${column}_${asc ? "desc" : "asc"}`)}
      >
        <span className="truncate">{label}</span>
        <Icon className="size-3 shrink-0" aria-hidden />
      </button>
    );
  };

  return (
    <div className="space-y-3">
      <div
        className={cn(
          "grid gap-2",
          detail
            ? "grid-cols-1 sm:grid-cols-2 lg:grid-cols-1"
            : "sm:grid-cols-2 xl:grid-cols-[minmax(0,2fr)_repeat(3,minmax(0,1fr))]",
        )}
      >
        <div className="relative min-w-0 flex-1">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/60"
            aria-hidden
          />
          <Input
            placeholder={t("apis.list.searchPlaceholder")}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(0);
            }}
            aria-label={t("apis.list.searchPlaceholder")}
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
            aria-label={t("apis.list.filterStatus")}
          >
            <SelectValue placeholder={t("accounts.list.statusPlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            {STATUS_FILTER_OPTIONS.map((option) => (
              <SelectItem key={option} value={option}>
                {option === "all"
                  ? t("accounts.list.allStatuses")
                  : t(STATUS_FILTER_LABEL_KEYS[option])}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={usageFilter}
          onValueChange={(value) => {
            setUsageFilter(value);
            setPage(0);
          }}
        >
          <SelectTrigger
            size="sm"
            className="w-full min-w-0"
            aria-label={t("apis.list.filterUsage")}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {["all", "unused", "used"].map((value) => (
              <SelectItem key={value} value={value}>
                {t(`apis.list.usage.${value}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={sortMode}
          onValueChange={(value) => changeSort(value as ApiSortMode)}
        >
          <SelectTrigger
            size="sm"
            className="w-full min-w-0"
            aria-label={t("apis.list.sort")}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="default">
              {t("apis.list.defaultOrder")}
            </SelectItem>
            {API_SORT_COLUMNS.flatMap((column) =>
              ["asc", "desc"].map((direction) => (
                <SelectItem
                  key={`${column}_${direction}`}
                  value={`${column}_${direction}`}
                >
                  {t(`apis.list.column.${column}`)} (
                  {t(`accounts.sortDirection.${direction}`)})
                </SelectItem>
              )),
            )}
          </SelectContent>
        </Select>
      </div>

      {!readOnly && (<Button
        type="button"
        size="sm"
        onClick={onOpenCreate}
        className={cn("h-8 gap-1.5 text-xs", detail && "w-full")}
      >
        <Plus className="h-3.5 w-3.5" />
        {t("apis.list.createKey")}
      </Button>)}

      {!detail && filtered.length > 0 ? (
        <div
          data-testid="api-list-headers"
          className={cn(
            "hidden items-center gap-3 rounded-lg bg-muted/50 px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground lg:grid",
            API_LIST_COLUMNS,
          )}
        >
          {API_SORT_COLUMNS.map(sortHeader)}
          <span>{t("apis.list.column.quota")}</span>
        </div>
      ) : null}
      <div
        className={
          detail
            ? "max-h-[calc(100vh-16rem)] space-y-1 overflow-y-auto p-1"
            : "space-y-2"
        }
      >
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed p-6 text-center">
            <p className="text-sm font-medium text-muted-foreground">
              {apiKeys.length === 0
                ? t("apis.list.emptyTitle")
                : t("apis.list.noMatches")}
            </p>
            <p className="text-xs text-muted-foreground/70">
              {apiKeys.length === 0
                ? t("apis.list.emptyDescription")
                : t("accounts.list.adjustFilters")}
            </p>
          </div>
        ) : (
          visible.map((apiKey) =>
            detail ? (
              <ApiListItem
                key={apiKey.id}
                apiKey={apiKey}
                selected={apiKey.id === selectedKeyId}
                onSelect={onSelect}
              />
            ) : (
              <ApiListOverviewRow
                key={apiKey.id}
                apiKey={apiKey}
                selected={apiKey.id === selectedKeyId}
                onSelect={onSelect}
              />
            ),
          )
        )}
      </div>
      {!detail && filtered.length > 0 ? (
        <nav
          aria-label={t("apis.list.pagination")}
          className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"
        >
          <span>
            {t("apis.list.range", {
              start: start + 1,
              end: Math.min(start + 24, filtered.length),
              total: filtered.length,
            })}
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={currentPage === 0}
              onClick={() => setPage(currentPage - 1)}
            >
              {t("accounts.grid.previous")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={start + 24 >= filtered.length}
              onClick={() => setPage(currentPage + 1)}
            >
              {t("accounts.grid.next")}
            </Button>
          </div>
        </nav>
      ) : null}
    </div>
  );
}
