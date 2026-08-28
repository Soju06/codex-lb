import { RotateCcw, Search } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MultiSelectFilter, type MultiSelectOption } from "@/features/dashboard/components/filters/multi-select-filter";
import type {
  AutomationJobsFilterState,
  AutomationRunsFilterState,
} from "@/features/automations/hooks/use-automation-listing";

export type AutomationJobsFiltersProps = {
  filters: AutomationJobsFilterState;
  accountOptions: MultiSelectOption[];
  modelOptions: MultiSelectOption[];
  statusOptions: MultiSelectOption[];
  scheduleTypeOptions: MultiSelectOption[];
  onSearchChange: (value: string) => void;
  onAccountChange: (values: string[]) => void;
  onModelChange: (values: string[]) => void;
  onStatusChange: (values: string[]) => void;
  onScheduleTypeChange: (values: string[]) => void;
  onReset: () => void;
};

export function AutomationJobsFilters({
  filters,
  accountOptions,
  modelOptions,
  statusOptions,
  scheduleTypeOptions,
  onSearchChange,
  onAccountChange,
  onModelChange,
  onStatusChange,
  onScheduleTypeChange,
  onReset,
}: AutomationJobsFiltersProps) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2 rounded-xl border bg-card p-4">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground/60" aria-hidden="true" />
          <Input
            value={filters.search}
            onChange={(event) => onSearchChange(event.target.value)}
            className="h-8 pl-9"
	            placeholder={t("automations.filters.jobsSearchPlaceholder")}
	            aria-label={t("automations.filters.jobsSearchAria")}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <MultiSelectFilter
	          label={t("automations.jobs.columns.accounts")}
          values={filters.accountIds}
          options={accountOptions}
          onChange={onAccountChange}
        />
        <MultiSelectFilter
	          label={t("apiKeys.table.models")}
          values={filters.models}
          options={modelOptions}
          onChange={onModelChange}
        />
        <MultiSelectFilter
	          label={t("automations.filters.statuses")}
          values={filters.statuses}
          options={statusOptions}
          onChange={onStatusChange}
        />
        <MultiSelectFilter
	          label={t("automations.filters.type")}
          values={filters.scheduleTypes}
          options={scheduleTypeOptions}
          onChange={onScheduleTypeChange}
        />
        <Button type="button" variant="ghost" size="sm" onClick={onReset} className="h-8 gap-1.5 text-xs text-muted-foreground">
          <RotateCcw className="h-3 w-3" aria-hidden="true" />
	          {t("common.actions.reset")}
        </Button>
      </div>
    </div>
  );
}

export type AutomationRunsFiltersProps = {
  filters: AutomationRunsFilterState;
  accountOptions: MultiSelectOption[];
  modelOptions: MultiSelectOption[];
  statusOptions: MultiSelectOption[];
  triggerOptions: MultiSelectOption[];
  onSearchChange: (value: string) => void;
  onAccountChange: (values: string[]) => void;
  onModelChange: (values: string[]) => void;
  onStatusChange: (values: string[]) => void;
  onTriggerChange: (values: string[]) => void;
  onReset: () => void;
};

export function AutomationRunsFilters({
  filters,
  accountOptions,
  modelOptions,
  statusOptions,
  triggerOptions,
  onSearchChange,
  onAccountChange,
  onModelChange,
  onStatusChange,
  onTriggerChange,
  onReset,
}: AutomationRunsFiltersProps) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2 rounded-xl border bg-card p-4">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground/60" aria-hidden="true" />
          <Input
            value={filters.search}
            onChange={(event) => onSearchChange(event.target.value)}
            className="h-8 pl-9"
	            placeholder={t("automations.filters.runsSearchPlaceholder")}
	            aria-label={t("automations.filters.runsSearchAria")}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <MultiSelectFilter
	          label={t("automations.jobs.columns.accounts")}
          values={filters.accountIds}
          options={accountOptions}
          onChange={onAccountChange}
        />
        <MultiSelectFilter
	          label={t("apiKeys.table.models")}
          values={filters.models}
          options={modelOptions}
          onChange={onModelChange}
        />
        <MultiSelectFilter
	          label={t("automations.filters.statuses")}
          values={filters.statuses}
          options={statusOptions}
          onChange={onStatusChange}
        />
        <MultiSelectFilter
	          label={t("automations.filters.triggers")}
          values={filters.triggers}
          options={triggerOptions}
          onChange={onTriggerChange}
        />
        <Button type="button" variant="ghost" size="sm" onClick={onReset} className="h-8 gap-1.5 text-xs text-muted-foreground">
          <RotateCcw className="h-3 w-3" aria-hidden="true" />
	          {t("common.actions.reset")}
        </Button>
      </div>
    </div>
  );
}
