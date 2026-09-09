import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { InheritBadge } from "@/features/settings/components/inherit-badge";
import { useSettings } from "@/features/settings/hooks/use-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { SettingsUpdateRequest } from "@/features/settings/schemas";

const SWITCH_ID = "automations-pause-all";

/**
 * "Pause all automations" switch in the Automations page header.
 *
 * Wired to the same dashboard setting as Settings → Advanced → Background jobs
 * (`automations_scheduler_enabled`): on pauses the scheduler tick and refuses
 * manual runs on every replica from the next tick, without a restart. The
 * inheritance badge and reset action are the shared ones.
 */
export function AutomationsPauseToggle() {
  const { t } = useTranslation();
  const { settingsQuery, updateSettingsMutation } = useSettings();
  const settings = settingsQuery.data;
  if (!settings) {
    return null;
  }
  const paused = !settings.automationsSchedulerEnabled;
  const busy = updateSettingsMutation.isPending;
  const save = (payload: SettingsUpdateRequest) => updateSettingsMutation.mutateAsync(payload).then(() => undefined);

  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Label htmlFor={SWITCH_ID} className="text-sm font-medium">
            {t("automations.pause.label")}
          </Label>
          {paused ? <Badge variant="destructive">{t("automations.pause.badge")}</Badge> : null}
        </div>
        <p className="text-xs text-muted-foreground">
          {paused ? t("automations.pause.pausedDescription") : t("automations.pause.description")}
        </p>
        <InheritBadge
          settings={settings}
          name="automations_scheduler_enabled"
          field="automationsSchedulerEnabled"
          busy={busy}
          onSave={save}
        />
      </div>
      <Switch
        id={SWITCH_ID}
        aria-label={t("automations.pause.ariaLabel")}
        checked={paused}
        disabled={busy}
        onCheckedChange={(checked) =>
          void save(buildSettingsUpdateRequest(settings, { automationsSchedulerEnabled: !checked }))
        }
      />
    </div>
  );
}
