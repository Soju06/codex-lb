import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
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
 * inheritance badge and reset action are the shared ones. Writing the setting
 * needs write access, so a read-only viewer sees the state but cannot flip it.
 */
export function AutomationsPauseToggle() {
  const { t } = useTranslation();
  const canWrite = useAuthStore((state) => state.canWrite);
  const { settingsQuery, updateSettingsMutation } = useSettings();
  const settings = settingsQuery.data;
  if (!settings) {
    return null;
  }
  const paused = !settings.automationsSchedulerEnabled;
  // Shared "busy" for the switch and the badge's reset action, as on the
  // Settings page: a pending write, or no write permission at all.
  const busy = updateSettingsMutation.isPending || !canWrite;
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
        {/*
          The badge reports the provenance of `automations_scheduler_enabled`,
          whose on/off is the inverse of this switch's "paused"; the caption
          names the setting so "(on)" is never read as "paused".
        */}
        <span className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">{t("automations.pause.inheritPrefix")}</span>
          <InheritBadge
            settings={settings}
            name="automations_scheduler_enabled"
            field="automationsSchedulerEnabled"
            busy={busy}
            onSave={save}
          />
        </span>
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
