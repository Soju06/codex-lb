import { Upload } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Switch } from "@/components/ui/switch";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type ImportSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function ImportSettings({ settings, busy, onSave }: ImportSettingsProps) {
  const { t } = useTranslation();
  const save = (patch: Partial<SettingsUpdateRequest>) =>
    void onSave(buildSettingsUpdateRequest(settings, patch));

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <Upload className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">{t("settings.import.title")}</h3>
              <p className="text-xs text-muted-foreground">{t("settings.import.description")}</p>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between rounded-lg border p-3">
          <div>
            <p className="text-sm font-medium">{t("settings.import.allowDuplicates.label")}</p>
            <p className="text-xs text-muted-foreground">
              {t("settings.import.allowDuplicates.description")}
            </p>
          </div>
          <Switch
            checked={settings.importWithoutOverwrite}
            disabled={busy}
            onCheckedChange={(checked) => save({ importWithoutOverwrite: checked })}
          />
        </div>
      </div>
    </section>
  );
}
