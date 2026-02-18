import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const TIMEFRAME_VALUES = ["all", "1h", "24h", "7d"] as const;
export type TimeframeValue = (typeof TIMEFRAME_VALUES)[number];

function isTimeframeValue(value: string): value is TimeframeValue {
  return (TIMEFRAME_VALUES as readonly string[]).includes(value);
}

export type TimeframeSelectProps = {
  value: TimeframeValue;
  onChange: (value: TimeframeValue) => void;
};

export function TimeframeSelect({ value, onChange }: TimeframeSelectProps) {
  return (
    <Select value={value} onValueChange={(next) => { if (isTimeframeValue(next)) onChange(next); }}>
      <SelectTrigger size="sm" className="w-28">
        <SelectValue placeholder="Timeframe" />
      </SelectTrigger>
      <SelectContent align="start">
        <SelectItem value="all">All</SelectItem>
        <SelectItem value="1h">1h</SelectItem>
        <SelectItem value="24h">24h</SelectItem>
        <SelectItem value="7d">7d</SelectItem>
      </SelectContent>
    </Select>
  );
}
