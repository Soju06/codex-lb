import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { OverviewTimeframe } from "@/features/dashboard/schemas";

const OVERVIEW_TIMEFRAME_VALUES = ["1d", "7d", "30d"] as const;

function isOverviewTimeframe(value: string): value is OverviewTimeframe {
  return (OVERVIEW_TIMEFRAME_VALUES as readonly string[]).includes(value);
}

export type OverviewTimeframeSelectProps = {
  value: OverviewTimeframe;
  onChange: (value: OverviewTimeframe) => void;
};

export function OverviewTimeframeSelect({
  value,
  onChange,
}: OverviewTimeframeSelectProps) {
  return (
    <Select value={value} onValueChange={(next) => { if (isOverviewTimeframe(next)) onChange(next); }}>
      <SelectTrigger
        size="sm"
        className="w-28"
        aria-label="Overview timeframe"
      >
        <SelectValue placeholder="Overview" />
      </SelectTrigger>
      <SelectContent align="end">
        <SelectItem value="1d">1d</SelectItem>
        <SelectItem value="7d">7d</SelectItem>
        <SelectItem value="30d">30d</SelectItem>
      </SelectContent>
    </Select>
  );
}
