import { useState } from "react";
import { addDays, format } from "date-fns";
import { CalendarIcon, ChevronDown, Infinity } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

const PRESETS = [
  { label: "1 day", shortLabel: "1d", days: 1 },
  { label: "7 days", shortLabel: "7d", days: 7 },
  { label: "30 days", shortLabel: "30d", days: 30 },
  { label: "90 days", shortLabel: "90d", days: 90 },
  { label: "1 year", shortLabel: "1y", days: 365 },
] as const;

export type ExpiryPickerProps = {
  value: Date | null;
  onChange: (date: Date | null) => void;
};

export function ExpiryPicker({ value, onChange }: ExpiryPickerProps) {
  const [open, setOpen] = useState(false);
  const [showCalendar, setShowCalendar] = useState(false);

  const activePresetDays = getActivePreset(value);

  function handleNever() {
    onChange(null);
    setShowCalendar(false);
    setOpen(false);
  }

  function handlePreset(days: number) {
    const date = addDays(new Date(), days);
    date.setHours(23, 59, 59, 0);
    onChange(date);
    setShowCalendar(false);
    setOpen(false);
  }

  function handleCalendarSelect(date: Date | undefined) {
    if (date) {
      date.setHours(23, 59, 59, 0);
      onChange(date);
      setShowCalendar(false);
      setOpen(false);
    }
  }

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) setShowCalendar(false);
  }

  function getTriggerLabel(): string {
    if (!value) return "No expiration";
    const preset = PRESETS.find((p) => p.days === activePresetDays);
    if (preset) return `${preset.label} (${format(value, "yyyy-MM-dd")})`;
    return format(value, "yyyy-MM-dd");
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className={cn(
            "w-full justify-between font-normal",
            !value && "text-muted-foreground",
          )}
        >
          <span className="flex items-center gap-2">
            {value ? <CalendarIcon className="size-4" /> : <Infinity className="size-4" />}
            {getTriggerLabel()}
          </span>
          <ChevronDown className="size-4 opacity-50" />
        </Button>
      </PopoverTrigger>

      <PopoverContent className="w-auto min-w-[280px] p-1" align="start">
        {showCalendar ? (
          <div className="-m-1">
            <div className="border-b px-3 py-2">
              <button
                type="button"
                className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                onClick={() => setShowCalendar(false)}
              >
                &larr; Back to presets
              </button>
            </div>
            <Calendar
              mode="single"
              selected={value ?? undefined}
              onSelect={handleCalendarSelect}
              disabled={(date) => date < new Date(new Date().setHours(0, 0, 0, 0))}
              autoFocus
            />
          </div>
        ) : (
          <div>
            <OptionItem
              active={value === null}
              onClick={handleNever}
            >
              <Infinity className="size-4" />
              No expiration
            </OptionItem>

            <div className="bg-border -mx-1 my-1 h-px" />

            {PRESETS.map((preset) => (
              <OptionItem
                key={preset.days}
                active={activePresetDays === preset.days}
                onClick={() => handlePreset(preset.days)}
              >
                {preset.label}
                <span className="ml-auto text-xs text-muted-foreground">
                  {format(addDays(new Date(), preset.days), "yyyy-MM-dd")}
                </span>
              </OptionItem>
            ))}

            <div className="bg-border -mx-1 my-1 h-px" />

            <OptionItem
              active={value !== null && activePresetDays === null}
              onClick={() => setShowCalendar(true)}
            >
              <CalendarIcon className="size-4" />
              Custom date...
              {value && activePresetDays === null && (
                <span className="ml-auto text-xs text-muted-foreground">
                  {format(value, "yyyy-MM-dd")}
                </span>
              )}
            </OptionItem>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}

function OptionItem({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      className={cn(
        "flex w-full cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-hidden select-none transition-colors hover:bg-accent hover:text-accent-foreground",
        active && "bg-accent text-accent-foreground font-medium",
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function getActivePreset(value: Date | null): number | null {
  if (!value) return null;
  const now = new Date();
  const diffMs = value.getTime() - now.getTime();
  const diffDays = Math.round(diffMs / (1000 * 60 * 60 * 24));
  for (const preset of PRESETS) {
    if (Math.abs(diffDays - preset.days) <= 0) {
      return preset.days;
    }
  }
  return null;
}
