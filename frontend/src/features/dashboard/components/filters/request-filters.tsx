import { RotateCcw, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MultiSelectFilter, type MultiSelectOption } from "@/features/dashboard/components/filters/multi-select-filter";
import { TimeframeSelect } from "@/features/dashboard/components/filters/timeframe-select";
import type { FilterState } from "@/features/dashboard/schemas";

export type RequestFiltersProps = {
  filters: FilterState;
  accountOptions: MultiSelectOption[];
  modelOptions: MultiSelectOption[];
  statusOptions: MultiSelectOption[];
  onSearchChange: (value: string) => void;
  onTimeframeChange: (value: FilterState["timeframe"]) => void;
  onAccountChange: (values: string[]) => void;
  onModelChange: (values: string[]) => void;
  onStatusChange: (values: string[]) => void;
  onReset: () => void;
};

export function RequestFilters({
  filters,
  accountOptions,
  modelOptions,
  statusOptions,
  onSearchChange,
  onTimeframeChange,
  onAccountChange,
  onModelChange,
  onStatusChange,
  onReset,
}: RequestFiltersProps) {
  return (
    <div className="space-y-2 rounded-xl border bg-card p-4">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground/60" aria-hidden="true" />
          <Input
            value={filters.search}
            onChange={(event) => onSearchChange(event.target.value)}
            className="h-8 pl-9"
            placeholder="Search request id, account, model, error..."
          />
        </div>

        <TimeframeSelect value={filters.timeframe} onChange={onTimeframeChange} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
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

        <Button type="button" variant="ghost" size="sm" onClick={onReset} className="h-8 gap-1.5 text-xs text-muted-foreground">
          <RotateCcw className="h-3 w-3" aria-hidden="true" />
          Reset
        </Button>
      </div>
    </div>
  );
}
