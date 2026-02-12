import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export type TimeframeValue = "all" | "1h" | "24h" | "7d";

export type TimeframeSelectProps = {
  value: TimeframeValue;
  onChange: (value: TimeframeValue) => void;
};

export function TimeframeSelect({ value, onChange }: TimeframeSelectProps) {
  return (
    <Select value={value} onValueChange={(next) => onChange(next as TimeframeValue)}>
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
