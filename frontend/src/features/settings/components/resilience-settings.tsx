import { ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Switch } from "@/components/ui/switch";
import { InheritBadge } from "@/features/settings/components/inherit-badge";
import type { InheritableSettingField } from "@/features/settings/hooks/use-inheritable-setting";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type ResilienceSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

type ResilienceToggle = {
  /** Backend setting name: dashboard column, env alias and provenance key. */
  name: "soft_drain_enabled" | "deterministic_failover_enabled" | "circuit_breaker_enabled";
  field: "softDrainEnabled" | "deterministicFailoverEnabled" | "circuitBreakerEnabled";
  i18nKey: "softDrain" | "deterministicFailover" | "circuitBreaker";
};

const RESILIENCE_TOGGLES: readonly ResilienceToggle[] = [
  { name: "soft_drain_enabled", field: "softDrainEnabled", i18nKey: "softDrain" },
  { name: "deterministic_failover_enabled", field: "deterministicFailoverEnabled", i18nKey: "deterministicFailover" },
  { name: "circuit_breaker_enabled", field: "circuitBreakerEnabled", i18nKey: "circuitBreaker" },
];

/**
 * Soft drain, deterministic failover and the per-account circuit breaker.
 *
 * Each switch shows the effective value. Until the operator touches a switch
 * the value is inherited (environment variable or code default) and the badge
 * says so; flipping it stores a dashboard value, and "Reset to inherited"
 * clears it again. Changes apply to the next request on every replica.
 */
export function ResilienceSettings({ settings, busy, onSave }: ResilienceSettingsProps) {
  const { t } = useTranslation();
  const save = (patch: Partial<SettingsUpdateRequest>) =>
    void onSave(buildSettingsUpdateRequest(settings, patch));

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <ShieldCheck className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">{t("settings.resilience.title")}</h3>
            <p className="text-xs text-muted-foreground">{t("settings.resilience.description")}</p>
          </div>
        </div>

        {RESILIENCE_TOGGLES.map((toggle) => (
          <div key={toggle.name} className="flex items-center justify-between gap-3 rounded-lg border p-3">
            <div className="space-y-1">
              <p className="text-sm font-medium">{t(`settings.resilience.${toggle.i18nKey}.label`)}</p>
              <p className="text-xs text-muted-foreground">
                {t(`settings.resilience.${toggle.i18nKey}.description`)}
              </p>
              <InheritBadge
                settings={settings}
                name={toggle.name}
                field={toggle.field satisfies InheritableSettingField}
                busy={busy}
                onSave={onSave}
              />
            </div>
            <Switch
              aria-label={t(`settings.resilience.${toggle.i18nKey}.ariaLabel`)}
              checked={settings[toggle.field]}
              disabled={busy}
              onCheckedChange={(checked) => save({ [toggle.field]: checked })}
            />
          </div>
        ))}
      </div>
    </section>
  );
}
