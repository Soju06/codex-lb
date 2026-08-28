import { RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Switch } from "@/components/ui/switch";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type ResetCreditSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function ResetCreditSettings({ settings, busy, onSave }: ResetCreditSettingsProps) {
  const { t } = useTranslation();
  const save = (patch: Partial<SettingsUpdateRequest>) =>
    void onSave(buildSettingsUpdateRequest(settings, patch));

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <RotateCcw className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">{t("settings.resetCredits.title")}</h3>
              <p className="text-xs text-muted-foreground">{t("settings.resetCredits.description")}</p>
            </div>
          </div>
        </div>

        <ResetCreditSwitchRow
          label={t("settings.resetCredits.badges.label")}
          description={t("settings.resetCredits.badges.description")}
          ariaLabel={t("settings.resetCredits.badges.ariaLabel")}
          checked={settings.showResetCreditBadges}
          disabled={busy}
          onCheckedChange={(checked) => save({ showResetCreditBadges: checked })}
        />
        <ResetCreditSwitchRow
          label={t("settings.resetCredits.expiryBadge.label")}
          description={t("settings.resetCredits.expiryBadge.description")}
          ariaLabel={t("settings.resetCredits.expiryBadge.ariaLabel")}
          checked={settings.showResetCreditExpiryBadge}
          disabled={busy}
          onCheckedChange={(checked) => save({ showResetCreditExpiryBadge: checked })}
        />
        <ResetCreditSwitchRow
          label={t("settings.resetCredits.autoRedeem.label")}
          description={t("settings.resetCredits.autoRedeem.description")}
          ariaLabel={t("settings.resetCredits.autoRedeem.ariaLabel")}
          checked={settings.autoRedeemResetCreditsBeforeExpiry}
          disabled={busy}
          onCheckedChange={(checked) => save({ autoRedeemResetCreditsBeforeExpiry: checked })}
        />
      </div>
    </section>
  );
}

type ResetCreditSwitchRowProps = {
  label: string;
  description: string;
  ariaLabel: string;
  checked: boolean;
  disabled: boolean;
  onCheckedChange: (checked: boolean) => void;
};

function ResetCreditSwitchRow({
  label,
  description,
  ariaLabel,
  checked,
  disabled,
  onCheckedChange,
}: ResetCreditSwitchRowProps) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border p-3">
      <div>
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <Switch
        aria-label={ariaLabel}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
      />
    </div>
  );
}
