import { Monitor, Moon, Palette, Sun } from "lucide-react";

import { useThemeStore, type ThemePreference } from "@/hooks/use-theme";
import { cn } from "@/lib/utils";

const THEME_OPTIONS: { value: ThemePreference; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "auto", label: "System", icon: Monitor },
];

export function AppearanceSettings() {
  const preference = useThemeStore((s) => s.preference);
  const setTheme = useThemeStore((s) => s.setTheme);

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
              <p className="text-xs text-muted-foreground">Choose how the interface looks.</p>
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
      </div>
    </section>
  );
}
