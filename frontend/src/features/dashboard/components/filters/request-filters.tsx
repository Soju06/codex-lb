import { Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MultiSelectFilter, type MultiSelectOption } from "@/features/dashboard/components/filters/multi-select-filter";
import { PaginationControls } from "@/features/dashboard/components/filters/pagination-controls";
import { TimeframeSelect } from "@/features/dashboard/components/filters/timeframe-select";
import type { FilterState } from "@/features/dashboard/schemas";

export type RequestFiltersProps = {
  filters: FilterState;
  accountOptions: MultiSelectOption[];
  modelOptions: MultiSelectOption[];
  statusOptions: MultiSelectOption[];
  total: number;
  hasMore: boolean;
  onSearchChange: (value: string) => void;
  onTimeframeChange: (value: FilterState["timeframe"]) => void;
  onAccountChange: (values: string[]) => void;
  onModelChange: (values: string[]) => void;
  onStatusChange: (values: string[]) => void;
  onLimitChange: (limit: number) => void;
  onOffsetChange: (offset: number) => void;
  onReset: () => void;
};

export function RequestFilters({
  filters,
  accountOptions,
  modelOptions,
  statusOptions,
  total,
  hasMore,
  onSearchChange,
  onTimeframeChange,
  onAccountChange,
  onModelChange,
  onStatusChange,
  onLimitChange,
  onOffsetChange,
  onReset,
}: RequestFiltersProps) {
  return (
    <div className="space-y-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-64 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={filters.search}
            onChange={(event) => onSearchChange(event.target.value)}
            className="h-8 pl-8"
            placeholder="Search request id, account, model, error..."
          />
        </div>

        <TimeframeSelect value={filters.timeframe} onChange={onTimeframeChange} />

        <MultiSelectFilter
          label="Accounts"
          values={filters.accountIds}
          options={accountOptions}
          onChange={onAccountChange}
        />
        <MultiSelectFilter
          label="Models"
          values={filters.modelOptions}
          options={modelOptions}
          onChange={onModelChange}
        />
        <MultiSelectFilter
          label="Statuses"
          values={filters.statuses}
          options={statusOptions}
          onChange={onStatusChange}
        />

        <Button type="button" variant="ghost" size="sm" onClick={onReset}>
          <X className="mr-1 h-3.5 w-3.5" />
          Reset
        </Button>
      </div>

      <div className="flex justify-end">
        <PaginationControls
          total={total}
          limit={filters.limit}
          offset={filters.offset}
          hasMore={hasMore}
          onLimitChange={onLimitChange}
          onOffsetChange={onOffsetChange}
        />
      </div>
    </div>
  );
}
