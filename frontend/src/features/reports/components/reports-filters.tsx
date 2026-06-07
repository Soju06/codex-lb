import { Button } from "@/components/ui/button";
import {
  MultiSelectFilter,
  type MultiSelectOption,
} from "@/features/dashboard/components/filters/multi-select-filter";
import { daysAgoLocalISO, localDateISO } from "../date";

export type ReportsFiltersState = {
  startDate: string;
  endDate: string;
  accountId: string[];
  model: string;
};

export type ReportsFiltersProps = {
  filters: ReportsFiltersState;
  accountOptions: MultiSelectOption[];
  modelOptions: MultiSelectOption[];
  onFiltersChange: (filters: ReportsFiltersState) => void;
};

const PRESETS = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
] as const;

export function ReportsFilters({
  filters,
  accountOptions,
  modelOptions,
  onFiltersChange,
}: ReportsFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card p-3">
      {PRESETS.map((preset) => (
        <Button
          key={preset.days}
          variant="outline"
          size="sm"
          onClick={() =>
            onFiltersChange({
              ...filters,
              startDate: daysAgoLocalISO(preset.days - 1),
              endDate: localDateISO(),
            })
          }
        >
          {preset.label}
        </Button>
      ))}

      <MultiSelectFilter
        label="Accounts"
        values={filters.accountId}
        options={accountOptions}
        onChange={(accountId) => onFiltersChange({ ...filters, accountId })}
      />
      <MultiSelectFilter
        label="Model"
        values={filters.model ? [filters.model] : []}
        options={modelOptions}
        onChange={(models) =>
          onFiltersChange({ ...filters, model: models.at(-1) ?? "" })
        }
      />

      <div className="ml-auto flex items-center gap-2">
        <input
          type="date"
          value={filters.startDate}
          onChange={(e) => onFiltersChange({ ...filters, startDate: e.target.value })}
          className="h-8 rounded-md border bg-transparent px-2 text-xs text-foreground"
        />
        <span className="text-xs text-muted-foreground">—</span>
        <input
          type="date"
          value={filters.endDate}
          onChange={(e) => onFiltersChange({ ...filters, endDate: e.target.value })}
          className="h-8 rounded-md border bg-transparent px-2 text-xs text-foreground"
        />
      </div>
    </div>
  );
}
