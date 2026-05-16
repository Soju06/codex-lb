import { Monitor, Moon, Palette, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useAccountQuotaDisplayStore, type AccountQuotaDisplayPreference } from "@/hooks/use-account-quota-display";
import { useThemeStore, type ThemePreference } from "@/hooks/use-theme";
import { useTimeFormatStore, type TimeFormatPreference } from "@/hooks/use-time-format";
import { cn } from "@/lib/utils";

const THEME_OPTIONS: { value: ThemePreference; labelKey: string; icon: typeof Sun }[] = [
  { value: "light", labelKey: "settings.appearance.theme.light", icon: Sun },
  { value: "dark", labelKey: "settings.appearance.theme.dark", icon: Moon },
  { value: "auto", labelKey: "settings.appearance.theme.auto", icon: Monitor },
];

const TIME_FORMAT_OPTIONS: { value: TimeFormatPreference; labelKey: string }[] = [
  { value: "12h", labelKey: "settings.appearance.timeFormat.h12" },
  { value: "24h", labelKey: "settings.appearance.timeFormat.h24" },
];

const QUOTA_DISPLAY_OPTIONS: {
  value: AccountQuotaDisplayPreference;
  labelKey: string;
  descriptionKey: string;
}[] = [
  {
    value: "5h",
    labelKey: "settings.appearance.accountRows.fiveHour",
    descriptionKey: "settings.appearance.accountRows.fiveHourDescription",
  },
  {
    value: "weekly",
    labelKey: "settings.appearance.accountRows.weekly",
    descriptionKey: "settings.appearance.accountRows.weeklyDescription",
  },
  {
    value: "both",
    labelKey: "settings.appearance.accountRows.both",
    descriptionKey: "settings.appearance.accountRows.bothDescription",
  },
];

export function AppearanceSettings() {
  const { t } = useTranslation();
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
              <h3 className="text-sm font-semibold">{t("settings.appearance.title")}</h3>
              <p className="text-xs text-muted-foreground">{t("settings.appearance.description")}</p>
            </div>
          </div>
        </div>

        <div className="divide-y rounded-lg border">
          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">{t("settings.appearance.theme.label")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.appearance.theme.description")}</p>
            </div>
            <div className="flex items-center gap-1 rounded-lg border border-border/50 bg-muted/40 p-0.5">
              {THEME_OPTIONS.map(({ value, labelKey, icon: Icon }) => (
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
                  {t(labelKey)}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">{t("settings.appearance.timeFormat.label")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.appearance.timeFormat.description")}</p>
            </div>
            <div className="flex items-center gap-1 rounded-lg border border-border/50 bg-muted/40 p-0.5">
              {TIME_FORMAT_OPTIONS.map(({ value, labelKey }) => (
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
                  <span className="block">{t(labelKey)}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">{t("settings.appearance.accountRows.label")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.appearance.accountRows.description")}</p>
            </div>
            <div className="flex items-center gap-1 rounded-lg border border-border/50 bg-muted/40 p-0.5">
              {QUOTA_DISPLAY_OPTIONS.map(({ value, labelKey, descriptionKey }) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={quotaDisplay === value}
                  title={t(descriptionKey)}
                  onClick={() => setQuotaDisplay(value)}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-left text-xs font-medium transition-colors duration-200",
                    quotaDisplay === value
                      ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <span className="block">{t(labelKey)}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
