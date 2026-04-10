import { Clock3, Monitor, Moon, Palette, Sun } from "lucide-react";

import { useThemeStore, type ThemePreference } from "@/hooks/use-theme";
import { useTimeFormatStore, type TimeFormatPreference } from "@/hooks/use-time-format";
import { cn } from "@/lib/utils";

const THEME_OPTIONS: { value: ThemePreference; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "auto", label: "System", icon: Monitor },
];

const TIME_FORMAT_OPTIONS: { value: TimeFormatPreference; label: string; preview: string }[] = [
  { value: "12h", label: "12h", preview: "03:45 PM" },
  { value: "24h", label: "24h", preview: "15:45" },
];

export function AppearanceSettings() {
  const preference = useThemeStore((s) => s.preference);
  const setTheme = useThemeStore((s) => s.setTheme);
  const timeFormat = useTimeFormatStore((s) => s.timeFormat);
  const setTimeFormat = useTimeFormatStore((s) => s.setTimeFormat);

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

        <div className="flex items-center justify-between rounded-lg border p-3">
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

        <div className="flex items-center justify-between rounded-lg border p-3">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 flex h-7 w-7 items-center justify-center rounded-md bg-primary/10">
              <Clock3 className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
            </div>
            <div>
              <p className="text-sm font-medium">Time format</p>
              <p className="text-xs text-muted-foreground">Apply 12h or 24h formatting to datetimes across the dashboard.</p>
            </div>
          </div>
          <div className="flex items-center gap-1 rounded-lg border border-border/50 bg-muted/40 p-0.5">
            {TIME_FORMAT_OPTIONS.map(({ value, label, preview }) => (
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
                <span className="block text-[10px] font-normal text-muted-foreground">{preview}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
