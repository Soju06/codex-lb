import { Grid2X2, List, Maximize, Minimize, Table2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import type { DashboardAccountViewMode } from "@/hooks/use-dashboard-preferences";

type AccountViewModeToggleProps = {
  value: DashboardAccountViewMode;
  onChange: (value: DashboardAccountViewMode) => void;
  fullscreen: boolean;
  onFullscreenChange: (fullscreen: boolean) => void;
};

const OPTIONS: Array<{ value: DashboardAccountViewMode; labelKey: string; icon: typeof Grid2X2 }> = [
  { value: "cards", labelKey: "dashboard.accounts.viewCards", icon: Grid2X2 },
  { value: "list", labelKey: "dashboard.accounts.viewList", icon: List },
  { value: "compact", labelKey: "dashboard.accounts.viewCompact", icon: Table2 },
];

export function AccountViewModeToggle({ value, onChange, fullscreen, onFullscreenChange }: AccountViewModeToggleProps) {
  const { t } = useTranslation();
  const FullscreenIcon = fullscreen ? Minimize : Maximize;

  return (
    <div className="inline-flex items-center gap-2">
      <div
        className="inline-flex h-8 items-center rounded-md border bg-background p-0.5"
        role="radiogroup"
        aria-label={t("dashboard.accounts.viewMode")}
      >
        {OPTIONS.map((option) => {
          const Icon = option.icon;
          const selected = value === option.value;
          const label = t(option.labelKey);
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={selected}
              aria-label={label}
              title={label}
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
      <button
        type="button"
        aria-pressed={fullscreen}
        aria-label={t("dashboard.accounts.viewFullscreen")}
        title={t(fullscreen ? "dashboard.accounts.exitFullscreen" : "dashboard.accounts.viewFullscreen")}
        onClick={() => onFullscreenChange(!fullscreen)}
        className={cn(
          "inline-flex h-8 w-8 items-center justify-center rounded-md border transition-colors",
          fullscreen
            ? "bg-accent text-accent-foreground shadow-sm"
            : "bg-background text-muted-foreground hover:bg-accent/60 hover:text-accent-foreground",
        )}
      >
        <FullscreenIcon className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
    </div>
  );
}
