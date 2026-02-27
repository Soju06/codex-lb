import { Route } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type RoutingSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function RoutingSettings({ settings, busy, onSave }: RoutingSettingsProps) {
  const save = (patch: Partial<SettingsUpdateRequest>) =>
    void onSave({
      stickyThreadsEnabled: settings.stickyThreadsEnabled,
      preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
      routingStrategy: settings.routingStrategy,
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
              <Route className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">Routing</h3>
              <p className="text-xs text-muted-foreground">Control how requests are distributed across accounts.</p>
            </div>
          </div>
        </div>

        <div className="divide-y rounded-lg border">
          <div className="flex items-center justify-between gap-4 p-3">
            <div>
              <p className="text-sm font-medium">Routing strategy</p>
              <p className="text-xs text-muted-foreground">Choose usage-based balancing or strict round robin.</p>
            </div>
            <Select
              value={settings.routingStrategy}
              onValueChange={(value) => save({ routingStrategy: value as "usage_weighted" | "round_robin" })}
            >
              <SelectTrigger className="h-8 w-44 text-xs" disabled={busy}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                <SelectItem value="usage_weighted">Usage weighted</SelectItem>
                <SelectItem value="round_robin">Round robin</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center justify-between p-3">
            <div>
              <p className="text-sm font-medium">Sticky threads</p>
              <p className="text-xs text-muted-foreground">Keep related requests on the same account.</p>
            </div>
            <Switch
              checked={settings.stickyThreadsEnabled}
              disabled={busy}
              onCheckedChange={(checked) => save({ stickyThreadsEnabled: checked })}
            />
          </div>

          <div className="flex items-center justify-between p-3">
            <div>
              <p className="text-sm font-medium">Prefer earlier reset</p>
              <p className="text-xs text-muted-foreground">Bias traffic to accounts with earlier quota reset.</p>
            </div>
            <Switch
              checked={settings.preferEarlierResetAccounts}
              disabled={busy}
              onCheckedChange={(checked) => save({ preferEarlierResetAccounts: checked })}
            />
          </div>
        </div>
      </div>
    </section>
  );
}
