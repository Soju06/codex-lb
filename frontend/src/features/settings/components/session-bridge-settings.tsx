import { Flame } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Switch } from "@/components/ui/switch";
import { InheritBadge } from "@/features/settings/components/inherit-badge";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type SessionBridgeSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

/**
 * Codex HTTP-bridge session settings that moved from the environment to the
 * dashboard (M3 codex prewarm).
 *
 * The switch shows the effective value. Until the operator touches it the value
 * is inherited (environment variable or code default) and the badge says so;
 * flipping it stores a dashboard value, and "Reset to inherited" clears it
 * again. Changes apply to the next new Codex session on every replica.
 */
export function SessionBridgeSettings({ settings, busy, onSave }: SessionBridgeSettingsProps) {
  const { t } = useTranslation();
  const save = (patch: Partial<SettingsUpdateRequest>) =>
    void onSave(buildSettingsUpdateRequest(settings, patch));

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <Flame className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">{t("settings.sessionBridge.title")}</h3>
            <p className="text-xs text-muted-foreground">{t("settings.sessionBridge.description")}</p>
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 rounded-lg border p-3">
          <div className="space-y-1">
            <p className="text-sm font-medium">{t("settings.sessionBridge.codexPrewarm.label")}</p>
            <p className="text-xs text-muted-foreground">
              {t("settings.sessionBridge.codexPrewarm.description")}
            </p>
            <InheritBadge
              settings={settings}
              name="http_responses_session_bridge_codex_prewarm_enabled"
              field="httpResponsesSessionBridgeCodexPrewarmEnabled"
              busy={busy}
              onSave={onSave}
            />
          </div>
          <Switch
            aria-label={t("settings.sessionBridge.codexPrewarm.ariaLabel")}
            checked={settings.httpResponsesSessionBridgeCodexPrewarmEnabled}
            disabled={busy}
            onCheckedChange={(checked) => save({ httpResponsesSessionBridgeCodexPrewarmEnabled: checked })}
          />
        </div>
      </div>
    </section>
  );
}
