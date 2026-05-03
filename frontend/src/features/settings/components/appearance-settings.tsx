import { Monitor, Moon, Palette, Sun } from "lucide-react";

import { Switch } from "@/components/ui/switch";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import { useAccountQuotaDisplayStore, type AccountQuotaDisplayPreference } from "@/hooks/use-account-quota-display";
import { useThemeStore, type ThemePreference } from "@/hooks/use-theme";
import { useTimeFormatStore, type TimeFormatPreference } from "@/hooks/use-time-format";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";
import { cn } from "@/lib/utils";

const THEME_OPTIONS: { value: ThemePreference; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "auto", label: "System", icon: Monitor },
];

const TIME_FORMAT_OPTIONS: { value: TimeFormatPreference; label: string }[] = [
  { value: "12h", label: "12h" },
  { value: "24h", label: "24h" },
];

const QUOTA_DISPLAY_OPTIONS: { value: AccountQuotaDisplayPreference; label: string; description: string }[] = [
  { value: "5h", label: "5H", description: "Show only the 5h quota row when available." },
  { value: "weekly", label: "W", description: "Show only the weekly quota row." },
  { value: "both", label: "Both", description: "Show both quota rows." },
];

export type AppearanceSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function AppearanceSettings({ settings, busy, onSave }: AppearanceSettingsProps) {
  const preference = useThemeStore((s) => s.preference);
  const setTheme = useThemeStore((s) => s.setTheme);
  const timeFormat = useTimeFormatStore((s) => s.timeFormat);
  const setTimeFormat = useTimeFormatStore((s) => s.setTimeFormat);
  const quotaDisplay = useAccountQuotaDisplayStore((s) => s.quotaDisplay);
  const setQuotaDisplay = useAccountQuotaDisplayStore((s) => s.setQuotaDisplay);

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <Palette className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">Appearance</h3>
              <p className="text-xs text-muted-foreground">Choose how the interface looks and how time is displayed.</p>
            </div>
          </div>
        </div>

        <div className="divide-y rounded-lg border">
          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">Theme</p>
              <p className="text-xs text-muted-foreground">Select your preferred color scheme.</p>
            </div>
            <div className="flex items-center gap-1 rounded-lg border border-border/50 bg-muted/40 p-0.5">
              {THEME_OPTIONS.map(({ value, label, icon: Icon }) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={preference === value}
                  onClick={() => setTheme(value)}
                  className={cn(
                    "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors duration-200",
                    preference === value
                      ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">Time format</p>
              <p className="text-xs text-muted-foreground">Apply 12h or 24h formatting to datetimes across the dashboard.</p>
            </div>
            <div className="flex items-center gap-1 rounded-lg border border-border/50 bg-muted/40 p-0.5">
              {TIME_FORMAT_OPTIONS.map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={timeFormat === value}
                  onClick={() => setTimeFormat(value)}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-left text-xs font-medium transition-colors duration-200",
                    timeFormat === value
                      ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <span className="block">{label}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">Account rows</p>
              <p className="text-xs text-muted-foreground">Choose which quota rows appear in compact account views.</p>
            </div>
            <div className="flex items-center gap-1 rounded-lg border border-border/50 bg-muted/40 p-0.5">
              {QUOTA_DISPLAY_OPTIONS.map(({ value, label, description }) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={quotaDisplay === value}
                  title={description}
                  onClick={() => setQuotaDisplay(value)}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-left text-xs font-medium transition-colors duration-200",
                    quotaDisplay === value
                      ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <span className="block">{label}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center justify-between gap-4 p-3">
            <div>
              <p className="text-sm font-medium">Priorities</p>
              <p className="text-xs text-muted-foreground">
                Show gold, silver, and bronze medals and use them for account selection.
              </p>
            </div>
            <Switch
              checked={settings.prioritiesEnabled}
              disabled={busy}
              onCheckedChange={(checked) => void onSave(buildSettingsUpdateRequest(settings, { prioritiesEnabled: checked }))}
            />
          </div>
        </div>
      </div>
    </section>
  );
}
