import { Button } from "@/components/ui/button";
import {
  MultiSelectFilter,
  type MultiSelectOption,
} from "@/features/dashboard/components/filters/multi-select-filter";
import { localDateISO } from "../date";

export type ReportsFiltersState = {
  startDate: string;
  endDate: string;
  accountId: string[];
  model: string;
  useragent: string;
};

export type ReportsFiltersProps = {
  filters: ReportsFiltersState;
  selectedPresetDays: number | null;
  accountOptions: MultiSelectOption[];
  modelOptions: MultiSelectOption[];
  useragentOptions: MultiSelectOption[];
  onPresetSelect: (days: number) => void;
  onFiltersChange: (filters: ReportsFiltersState) => void;
};

const PRESETS = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
] as const;

export function ReportsFilters({
  filters,
  selectedPresetDays,
  accountOptions,
  modelOptions,
  useragentOptions,
  onPresetSelect,
  onFiltersChange,
}: ReportsFiltersProps) {
  const maxDate = localDateISO();

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card p-3">
      {PRESETS.map((preset) => {
        const isSelected = selectedPresetDays === preset.days;

        return (
          <Button
            key={preset.days}
            variant={isSelected ? "default" : "outline"}
            size="sm"
            aria-pressed={isSelected}
            onClick={() => onPresetSelect(preset.days)}
          >
            {preset.label}
          </Button>
        );
      })}

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
      <MultiSelectFilter
        label="UserAgent"
        values={filters.useragent ? [filters.useragent] : []}
        options={useragentOptions}
        onChange={(useragents) =>
          onFiltersChange({ ...filters, useragent: useragents.at(-1) ?? "" })
        }
      />

      <div className="ml-auto flex items-center gap-2">
        <input
          type="date"
          aria-label="Start date"
          max={maxDate}
          value={filters.startDate}
          onChange={(e) => onFiltersChange({ ...filters, startDate: e.target.value })}
          className="h-8 rounded-md border bg-transparent px-2 text-xs text-foreground"
        />
        <span className="text-xs text-muted-foreground">—</span>
        <input
          type="date"
          aria-label="End date"
          max={maxDate}
          value={filters.endDate}
          onChange={(e) => onFiltersChange({ ...filters, endDate: e.target.value })}
          className="h-8 rounded-md border bg-transparent px-2 text-xs text-foreground"
        />
      </div>
    </div>
  );
}
