import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type RoutingSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function RoutingSettings({ settings, busy, onSave }: RoutingSettingsProps) {
  const [stickyThreadsEnabled, setStickyThreadsEnabled] = useState(settings.stickyThreadsEnabled);
  const [preferEarlierResetAccounts, setPreferEarlierResetAccounts] = useState(
    settings.preferEarlierResetAccounts,
  );

  return (
    <section className="space-y-3 rounded-xl border p-4">
      <div>
        <h3 className="text-sm font-semibold">Routing</h3>
        <p className="text-xs text-muted-foreground">Control how requests are distributed across accounts.</p>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between rounded-md border p-2">
          <div>
            <p className="text-sm">Sticky threads</p>
            <p className="text-xs text-muted-foreground">Keep related requests on the same account.</p>
          </div>
          <Switch checked={stickyThreadsEnabled} onCheckedChange={setStickyThreadsEnabled} disabled={busy} />
        </div>

        <div className="flex items-center justify-between rounded-md border p-2">
          <div>
            <p className="text-sm">Prefer earlier reset</p>
            <p className="text-xs text-muted-foreground">Bias traffic to accounts with earlier quota reset.</p>
          </div>
          <Switch
            checked={preferEarlierResetAccounts}
            onCheckedChange={setPreferEarlierResetAccounts}
            disabled={busy}
          />
        </div>
      </div>

      <Button
        type="button"
        size="sm"
        onClick={() =>
          void onSave({
            stickyThreadsEnabled,
            preferEarlierResetAccounts,
            totpRequiredOnLogin: settings.totpRequiredOnLogin,
            apiKeyAuthEnabled: settings.apiKeyAuthEnabled,
          })
        }
        disabled={busy}
      >
        Save routing settings
      </Button>
    </section>
  );
}
