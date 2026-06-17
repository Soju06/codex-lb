import { Grid2X2, List } from "lucide-react";

import { cn } from "@/lib/utils";
import type { DashboardAccountViewMode } from "@/hooks/use-dashboard-preferences";

type AccountViewModeToggleProps = {
  value: DashboardAccountViewMode;
  onChange: (value: DashboardAccountViewMode) => void;
};

const OPTIONS: Array<{ value: DashboardAccountViewMode; label: string; icon: typeof Grid2X2 }> = [
  { value: "cards", label: "View accounts as cards", icon: Grid2X2 },
  { value: "list", label: "View accounts as list", icon: List },
];

export function AccountViewModeToggle({ value, onChange }: AccountViewModeToggleProps) {
  return (
    <div
      className="inline-flex h-8 items-center rounded-md border bg-background p-0.5"
      role="radiogroup"
      aria-label="Account view mode"
    >
      {OPTIONS.map((option) => {
        const Icon = option.icon;
        const selected = value === option.value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={option.label}
            title={option.label}
            onClick={() => onChange(option.value)}
            className={cn(
              "inline-flex h-6 w-7 items-center justify-center rounded-[5px] text-muted-foreground transition-colors",
              selected
                ? "bg-accent text-accent-foreground shadow-sm"
                : "hover:bg-accent/60 hover:text-accent-foreground",
            )}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}
