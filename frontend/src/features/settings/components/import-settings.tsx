import { Upload } from "lucide-react";

import { Switch } from "@/components/ui/switch";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type ImportSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function ImportSettings({ settings, busy, onSave }: ImportSettingsProps) {
  const save = (patch: Partial<SettingsUpdateRequest>) =>
    void onSave({
      stickyThreadsEnabled: settings.stickyThreadsEnabled,
      preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
      importWithoutOverwrite: settings.importWithoutOverwrite,
      totpRequiredOnLogin: settings.totpRequiredOnLogin,
      apiKeyAuthEnabled: settings.apiKeyAuthEnabled,
      ...patch,
    });

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <Upload className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">Import</h3>
              <p className="text-xs text-muted-foreground">Control how account import handles duplicates.</p>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between rounded-lg border p-3">
          <div>
            <p className="text-sm font-medium">Allow import without overwrite</p>
            <p className="text-xs text-muted-foreground">
              Keep duplicate imports as separate accounts instead of replacing existing ones.
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
