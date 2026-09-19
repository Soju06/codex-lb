import { useState } from "react";
import { Users } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type TeamSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function TeamSettings({ settings, busy, onSave }: TeamSettingsProps) {
  const [baseUrl, setBaseUrl] = useState(settings.teamPublicBaseUrl ?? "");

  const trimmed = baseUrl.trim();
  const changed = trimmed !== (settings.teamPublicBaseUrl ?? "");

  return (
    <section className="space-y-3 rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <Users className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">Team</h3>
          <p className="text-xs text-muted-foreground">
            Track named members above API keys and cap their aggregate usage.
          </p>
        </div>
      </div>

      <div className="flex items-center justify-between rounded-lg border p-3">
        <div className="space-y-1">
          <p className="text-sm font-medium">Team mode (keys required for untrusted clients)</p>
          <p className="text-xs text-muted-foreground">
            Trusted clients stay keyless. Everyone else must present a valid API key, even while
            API key authentication is off.
          </p>
        </div>
        <Switch
          checked={settings.teamModeEnabled}
          disabled={busy}
          aria-label="Team mode (keys required for untrusted clients)"
          onCheckedChange={(enabled) =>
            void onSave(buildSettingsUpdateRequest(settings, { teamModeEnabled: enabled }))
          }
        />
      </div>

      <div className="flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm font-medium">Public base URL</p>
          <p className="text-xs text-muted-foreground">
            Used in the onboarding snippets. Falls back to the dashboard origin when blank.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Input
            value={baseUrl}
            disabled={busy}
            placeholder="https://lb.example.com"
            aria-label="Public base URL"
            className="h-8 w-64 text-xs"
            onChange={(event) => setBaseUrl(event.target.value)}
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 text-xs"
            disabled={busy || !changed}
            onClick={() =>
              void onSave(
                buildSettingsUpdateRequest(settings, {
                  teamPublicBaseUrl: trimmed === "" ? null : trimmed,
                }),
              )
            }
          >
            Save base URL
          </Button>
        </div>
      </div>
    </section>
  );
}
