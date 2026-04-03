import { useEffect, useMemo, useState } from "react";
import { Eye, Shield } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type {
  DashboardSettings,
  RequestVisibilityMode,
  SettingsUpdateRequest,
} from "@/features/settings/schemas";

const TEMPORARY_OPTIONS = [
  { minutes: 15, label: "15 minutes" },
  { minutes: 60, label: "1 hour" },
  { minutes: 240, label: "4 hours" },
  { minutes: 1440, label: "24 hours" },
] as const;

export type RequestVisibilitySettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

function describeCurrentPolicy(settings: DashboardSettings) {
  if (settings.requestVisibilityMode === "persistent") {
    return { label: "On", tone: "default" as const, detail: "Captured request details are stored for future requests." };
  }
  if (settings.requestVisibilityMode === "temporary" && settings.requestVisibilityExpiresAt) {
    const formatted = new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(settings.requestVisibilityExpiresAt));
    return {
      label: settings.requestVisibilityEnabled ? "Temporary" : "Temporary expired",
      tone: settings.requestVisibilityEnabled ? ("secondary" as const) : ("outline" as const),
      detail: settings.requestVisibilityEnabled
        ? `Enabled until ${formatted}.`
        : `Expired at ${formatted}. Re-enable to capture future requests again.`,
    };
  }
  return { label: "Off", tone: "outline" as const, detail: "Request visibility capture is disabled." };
}

export function RequestVisibilitySettings({ settings, busy, onSave }: RequestVisibilitySettingsProps) {
  const [mode, setMode] = useState<RequestVisibilityMode>(settings.requestVisibilityMode);
  const [temporaryDuration, setTemporaryDuration] = useState<string>(String(TEMPORARY_OPTIONS[1].minutes));

  useEffect(() => {
    setMode(settings.requestVisibilityMode);
  }, [settings.requestVisibilityMode]);

  const policy = useMemo(() => describeCurrentPolicy(settings), [settings]);

  const durationMinutes = Number.parseInt(temporaryDuration, 10);
  const temporaryValid = Number.isInteger(durationMinutes) && durationMinutes > 0;
  const modeChanged = mode !== settings.requestVisibilityMode;
  const canSave = !busy && (mode !== "temporary" ? modeChanged : temporaryValid);

  const handleSave = () => {
    const patch: Partial<SettingsUpdateRequest> = { requestVisibilityMode: mode };
    if (mode === "temporary") {
      patch.requestVisibilityDurationMinutes = durationMinutes;
    }
    return onSave(buildSettingsUpdateRequest(settings, patch));
  };

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <Eye className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">Request visibility</h3>
              <p className="text-xs text-muted-foreground">
                Admin-only control for capturing selected request headers plus request payload details for future requests.
              </p>
            </div>
          </div>
          <Badge variant={policy.tone}>{policy.label}</Badge>
        </div>

        <div className="rounded-lg border">
          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">Current policy</p>
              <p className="text-xs text-muted-foreground">{policy.detail}</p>
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Shield className="h-3.5 w-3.5" aria-hidden="true" />
              Session-id header keys stay excluded; secret-like fields are redacted, while selected headers and other payload fields may remain visible.
            </div>
          </div>

          <div className="flex flex-col gap-3 border-t p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">Capture mode</p>
              <p className="text-xs text-muted-foreground">Choose whether request visibility is off, always on, or temporarily enabled.</p>
            </div>
            <Select value={mode} onValueChange={(value) => setMode(value as RequestVisibilityMode)}>
              <SelectTrigger className="h-8 w-48 text-xs" disabled={busy} aria-label="Request visibility mode">
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                <SelectItem value="off">Off</SelectItem>
                <SelectItem value="persistent">On</SelectItem>
                <SelectItem value="temporary">Temporary</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {mode === "temporary" ? (
            <div className="flex flex-col gap-3 border-t p-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-medium">Temporary duration</p>
                <p className="text-xs text-muted-foreground">This affects only future requests and turns off automatically after the selected window.</p>
              </div>
              <Select value={temporaryDuration} onValueChange={setTemporaryDuration}>
                <SelectTrigger className="h-8 w-40 text-xs" disabled={busy} aria-label="Temporary visibility duration">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent align="end">
                  {TEMPORARY_OPTIONS.map((option) => (
                    <SelectItem key={option.minutes} value={String(option.minutes)}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}

          <div className="flex items-center justify-end border-t p-3">
            <Button type="button" size="sm" variant="outline" disabled={!canSave} onClick={() => void handleSave()}>
              {mode === "temporary" ? "Enable temporarily" : "Save policy"}
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
