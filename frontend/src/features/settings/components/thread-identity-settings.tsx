import { Fingerprint } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Switch } from "@/components/ui/switch";
import { InheritBadge } from "@/features/settings/components/inherit-badge";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type ThreadIdentitySettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

/**
 * Account-scoped outbound thread identity.
 *
 * The switch shows the effective value. Until the operator touches it the value
 * is inherited (environment variable or code default, both off) and the badge
 * says so; flipping it stores a dashboard value, and "Reset to inherited"
 * clears it again. Turning it on changes the identifiers every pooled account
 * presents upstream, so the first turn of each live thread is a one-time cache
 * miss; turning it back off restores the previous identifiers exactly.
 */
export function ThreadIdentitySettings({ settings, busy, onSave }: ThreadIdentitySettingsProps) {
  const { t } = useTranslation();
  const save = (patch: Partial<SettingsUpdateRequest>) =>
    void onSave(buildSettingsUpdateRequest(settings, patch));

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <Fingerprint className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">{t("settings.threadIdentity.title")}</h3>
            <p className="text-xs text-muted-foreground">{t("settings.threadIdentity.description")}</p>
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 rounded-lg border p-3">
          <div className="space-y-1">
            <p className="text-sm font-medium">{t("settings.threadIdentity.accountScoped.label")}</p>
            <p className="text-xs text-muted-foreground">
              {t("settings.threadIdentity.accountScoped.description")}
            </p>
            <InheritBadge
              settings={settings}
              name="account_scoped_thread_identity_enabled"
              field="accountScopedThreadIdentityEnabled"
              busy={busy}
              onSave={onSave}
            />
          </div>
          <Switch
            aria-label={t("settings.threadIdentity.accountScoped.ariaLabel")}
            checked={settings.accountScopedThreadIdentityEnabled}
            disabled={busy}
            onCheckedChange={(checked) => save({ accountScopedThreadIdentityEnabled: checked })}
          />
        </div>
      </div>
    </section>
  );
}
