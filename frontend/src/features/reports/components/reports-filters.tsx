import { Button } from "@/components/ui/button";

export type ReportsFiltersState = {
  startDate: string;
  endDate: string;
  accountId: string[];
  model: string;
};

export type ReportsFiltersProps = {
  filters: ReportsFiltersState;
  onFiltersChange: (filters: ReportsFiltersState) => void;
};

const PRESETS = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
] as const;

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgoISO(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export function ReportsFilters({ filters, onFiltersChange }: ReportsFiltersProps) {
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
              startDate: daysAgoISO(preset.days),
              endDate: todayISO(),
            })
          }
        >
          {preset.label}
        </Button>
      ))}

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
