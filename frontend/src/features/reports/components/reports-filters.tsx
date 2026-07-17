import { useId } from "react";
import { Button } from "@/components/ui/button";
import { useTranslation } from "react-i18next";
import {
  MultiSelectFilter,
  type MultiSelectOption,
} from "@/features/dashboard/components/filters/multi-select-filter";
import { isReportDateRangeValid, localDateISO } from "../date";

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
  const { t } = useTranslation();
  const maxDate = localDateISO();
  const dateRangeErrorId = useId();
  const isDateRangeInvalid = !isReportDateRangeValid(
    filters.startDate,
    filters.endDate,
  );
  const startDateMax =
    filters.endDate && filters.endDate < maxDate ? filters.endDate : maxDate;

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
        label={t("dashboard.filters.accounts")}
        values={filters.accountId}
        options={accountOptions}
        onChange={(accountId) => onFiltersChange({ ...filters, accountId })}
      />
      <MultiSelectFilter
        label={t("dashboard.filters.model")}
        values={filters.model ? [filters.model] : []}
        options={modelOptions}
        onChange={(models) =>
          onFiltersChange({ ...filters, model: models.at(-1) ?? "" })
        }
      />
      <MultiSelectFilter
        label={t("reports.filters.userAgent")}
        values={filters.useragent ? [filters.useragent] : []}
        options={useragentOptions}
        onChange={(useragents) =>
          onFiltersChange({ ...filters, useragent: useragents.at(-1) ?? "" })
        }
      />

      <div className="ml-auto flex flex-col items-end gap-1">
        <div className="flex items-center gap-2">
          <input
            type="date"
            name="report-start-date"
            autoComplete="off"
            aria-label={t("reports.filters.startDate")}
            aria-invalid={isDateRangeInvalid || undefined}
            aria-describedby={isDateRangeInvalid ? dateRangeErrorId : undefined}
            max={startDateMax}
            value={filters.startDate}
            onChange={(e) =>
              onFiltersChange({ ...filters, startDate: e.target.value })
            }
            className="h-8 rounded-md border bg-transparent px-2 text-xs text-foreground aria-invalid:border-destructive aria-invalid:ring-destructive/20"
          />
          <span aria-hidden="true" className="text-xs text-muted-foreground">
            —
          </span>
          <input
            type="date"
            name="report-end-date"
            autoComplete="off"
            aria-label={t("reports.filters.endDate")}
            aria-invalid={isDateRangeInvalid || undefined}
            aria-describedby={isDateRangeInvalid ? dateRangeErrorId : undefined}
            min={filters.startDate || undefined}
            max={maxDate}
            value={filters.endDate}
            onChange={(e) =>
              onFiltersChange({ ...filters, endDate: e.target.value })
            }
            className="h-8 rounded-md border bg-transparent px-2 text-xs text-foreground aria-invalid:border-destructive aria-invalid:ring-destructive/20"
          />
        </div>
        {isDateRangeInvalid ? (
          <p
            id={dateRangeErrorId}
            aria-live="polite"
            className="text-xs text-destructive"
          >
            {t("reports.filters.invalidDateRange")}
          </p>
        ) : null}
      </div>
    </div>
  );
}
