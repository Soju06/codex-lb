import { Timer } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Switch } from "@/components/ui/switch";
import { InheritBadge } from "@/features/settings/components/inherit-badge";
import type { InheritableSettingField } from "@/features/settings/hooks/use-inheritable-setting";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type BackgroundJobsSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

type BackgroundJobToggle = {
  /** Backend setting name: dashboard column, env alias and provenance key. */
  name: "auth_guardian_enabled" | "automations_scheduler_enabled" | "rate_limit_reset_credits_refresh_enabled";
  field: "authGuardianEnabled" | "automationsSchedulerEnabled" | "rateLimitResetCreditsRefreshEnabled";
  i18nKey: "authGuardian" | "automations" | "resetCredits";
};

const BACKGROUND_JOB_TOGGLES: readonly BackgroundJobToggle[] = [
  { name: "auth_guardian_enabled", field: "authGuardianEnabled", i18nKey: "authGuardian" },
  { name: "automations_scheduler_enabled", field: "automationsSchedulerEnabled", i18nKey: "automations" },
  {
    name: "rate_limit_reset_credits_refresh_enabled",
    field: "rateLimitResetCreditsRefreshEnabled",
    i18nKey: "resetCredits",
  },
];

/**
 * Auth Guardian, the automations scheduler and reset-credit polling.
 *
 * Each switch shows the effective value. Until the operator touches a switch
 * the value is inherited (environment variable or code default) and the badge
 * says so; flipping it stores a dashboard value, and "Reset to inherited"
 * clears it again. The loops keep running and read the value at the start of
 * every cycle, so a change applies on the next tick without a restart. The
 * guardian additionally reports when the replica topology blocks it.
 */
export function BackgroundJobsSettings({ settings, busy, onSave }: BackgroundJobsSettingsProps) {
  const { t } = useTranslation();
  const save = (patch: Partial<SettingsUpdateRequest>) =>
    void onSave(buildSettingsUpdateRequest(settings, patch));

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <Timer className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">{t("settings.backgroundJobs.title")}</h3>
            <p className="text-xs text-muted-foreground">{t("settings.backgroundJobs.description")}</p>
          </div>
        </div>

        {BACKGROUND_JOB_TOGGLES.map((toggle) => {
          const blockedByTopology = toggle.name === "auth_guardian_enabled" && settings.authGuardianBlockedByTopology;
          return (
            <div key={toggle.name} className="flex items-center justify-between gap-3 rounded-lg border p-3">
              <div className="space-y-1">
                <p className="text-sm font-medium">{t(`settings.backgroundJobs.${toggle.i18nKey}.label`)}</p>
                <p className="text-xs text-muted-foreground">
                  {t(`settings.backgroundJobs.${toggle.i18nKey}.description`)}
                </p>
                {blockedByTopology ? (
                  <p role="note" className="text-xs text-amber-600 dark:text-amber-400">
                    {t("settings.backgroundJobs.authGuardian.blockedByTopology")}
                  </p>
                ) : null}
                <InheritBadge
                  settings={settings}
                  name={toggle.name}
                  field={toggle.field satisfies InheritableSettingField}
                  busy={busy}
                  onSave={onSave}
                />
              </div>
              <Switch
                aria-label={t(`settings.backgroundJobs.${toggle.i18nKey}.ariaLabel`)}
                checked={settings[toggle.field]}
                disabled={busy}
                onCheckedChange={(checked) => save({ [toggle.field]: checked })}
              />
            </div>
          );
        })}
      </div>
    </section>
  );
}
