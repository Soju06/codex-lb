import { useState } from "react";
import { Route, Zap } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

const LIMIT_WARMUP_MODEL_MAX_LENGTH = 128;
const LIMIT_WARMUP_PROMPT_MAX_LENGTH = 512;

export type RoutingSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function RoutingSettings({ settings, busy, onSave }: RoutingSettingsProps) {
  const [cacheAffinityTtl, setCacheAffinityTtl] = useState(
    String(settings.openaiCacheAffinityMaxAgeSeconds),
  );
  const [limitWarmupModel, setLimitWarmupModel] = useState(settings.limitWarmupModel);
  const [limitWarmupPrompt, setLimitWarmupPrompt] = useState(settings.limitWarmupPrompt);
  const [limitWarmupCooldown, setLimitWarmupCooldown] = useState(String(settings.limitWarmupCooldownSeconds));

  const save = (patch: Partial<SettingsUpdateRequest>) =>
    void onSave(buildSettingsUpdateRequest(settings, patch));

  const parsedCacheAffinityTtl = Number.parseInt(cacheAffinityTtl, 10);
  const cacheAffinityTtlValid = Number.isInteger(parsedCacheAffinityTtl) && parsedCacheAffinityTtl > 0;
  const cacheAffinityTtlChanged =
    cacheAffinityTtlValid && parsedCacheAffinityTtl !== settings.openaiCacheAffinityMaxAgeSeconds;
  const parsedLimitWarmupCooldown = Number(limitWarmupCooldown);
  const limitWarmupCooldownValid = Number.isInteger(parsedLimitWarmupCooldown) && parsedLimitWarmupCooldown >= 60;
  const limitWarmupFieldsChanged =
    limitWarmupModel.trim() !== settings.limitWarmupModel ||
    limitWarmupPrompt.trim() !== settings.limitWarmupPrompt ||
    (limitWarmupCooldownValid && parsedLimitWarmupCooldown !== settings.limitWarmupCooldownSeconds);
  const limitWarmupFieldsValid =
    limitWarmupModel.trim().length > 0 &&
    limitWarmupModel.trim().length <= LIMIT_WARMUP_MODEL_MAX_LENGTH &&
    limitWarmupPrompt.trim().length > 0 &&
    limitWarmupPrompt.trim().length <= LIMIT_WARMUP_PROMPT_MAX_LENGTH &&
    limitWarmupCooldownValid;

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
              <p className="text-sm font-medium">Upstream stream transport</p>
              <p className="text-xs text-muted-foreground">
                Choose how `codex-lb` connects upstream for streaming responses.
              </p>
            </div>
            <Select
              value={settings.upstreamStreamTransport}
              onValueChange={(value) =>
                save({ upstreamStreamTransport: value as "default" | "auto" | "http" | "websocket" })
              }
            >
              <SelectTrigger className="h-8 w-44 text-xs" disabled={busy}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                <SelectItem value="default">Server default</SelectItem>
                <SelectItem value="auto">Auto</SelectItem>
                <SelectItem value="http">Responses</SelectItem>
                <SelectItem value="websocket">WebSockets</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center justify-between gap-4 p-3">
            <div>
              <p className="text-sm font-medium">Routing strategy</p>
              <p className="text-xs text-muted-foreground">Choose how requests are distributed across accounts.</p>
            </div>
            <Select
              value={settings.routingStrategy}
              onValueChange={(value) => save({ routingStrategy: value as "usage_weighted" | "round_robin" | "capacity_weighted" })}
            >
              <SelectTrigger className="h-8 w-44 text-xs" disabled={busy}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                <SelectItem value="capacity_weighted">Capacity weighted</SelectItem>
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
              aria-label="Enable sticky threads"
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
              aria-label="Prefer earlier reset accounts"
              checked={settings.preferEarlierResetAccounts}
              disabled={busy}
              onCheckedChange={(checked) => save({ preferEarlierResetAccounts: checked })}
            />
          </div>

          <div className="space-y-3 p-3">
            <div className="flex items-center justify-between gap-4">
              <div className="flex min-w-0 items-center gap-2.5">
                <Zap className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
                <div>
                  <p className="text-sm font-medium">Limit warm-up</p>
                  <p className="text-xs text-muted-foreground">Send one reset-confirmed warm-up for opted-in accounts.</p>
                </div>
              </div>
              <Switch
                aria-label="Enable limit warm-up"
                checked={settings.limitWarmupEnabled}
                disabled={busy}
                onCheckedChange={(checked) => save({ limitWarmupEnabled: checked })}
              />
            </div>

            <div className="grid gap-2 sm:grid-cols-[10rem_minmax(0,1fr)_7rem]">
              <Select
                value={settings.limitWarmupWindows}
                onValueChange={(value) => save({ limitWarmupWindows: value as "primary" | "secondary" | "both" })}
              >
                <SelectTrigger className="h-8 text-xs" disabled={busy}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent align="start">
                  <SelectItem value="both">5h + weekly</SelectItem>
                  <SelectItem value="primary">5h only</SelectItem>
                  <SelectItem value="secondary">Weekly only</SelectItem>
                </SelectContent>
              </Select>
              <Input
                value={limitWarmupModel}
                disabled={busy}
                maxLength={LIMIT_WARMUP_MODEL_MAX_LENGTH}
                onChange={(event) => setLimitWarmupModel(event.target.value)}
                className="h-8 text-xs"
                aria-label="Warm-up model"
              />
              <Input
                type="number"
                min={60}
                step={60}
                inputMode="numeric"
                value={limitWarmupCooldown}
                disabled={busy}
                onChange={(event) => setLimitWarmupCooldown(event.target.value)}
                className="h-8 text-xs"
                aria-label="Warm-up cooldown"
              />
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                value={limitWarmupPrompt}
                disabled={busy}
                maxLength={LIMIT_WARMUP_PROMPT_MAX_LENGTH}
                onChange={(event) => setLimitWarmupPrompt(event.target.value)}
                className="h-8 text-xs"
                aria-label="Warm-up prompt"
              />
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs sm:w-24"
                disabled={busy || !limitWarmupFieldsChanged || !limitWarmupFieldsValid}
                onClick={() =>
                  void save({
                    limitWarmupModel: limitWarmupModel.trim(),
                    limitWarmupPrompt: limitWarmupPrompt.trim(),
                    limitWarmupCooldownSeconds: parsedLimitWarmupCooldown,
                  })
                }
              >
                Save
              </Button>
            </div>
          </div>

          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">Prompt-cache affinity TTL</p>
              <p className="text-xs text-muted-foreground">
                Keep OpenAI-style prompt-cache mappings warm for a bounded number of seconds.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Input
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                value={cacheAffinityTtl}
                disabled={busy}
                onChange={(event) => setCacheAffinityTtl(event.target.value)}
                aria-label="Prompt-cache affinity TTL"
                onKeyDown={(event) => {
                  if (event.key === "Enter" && cacheAffinityTtlChanged) {
                    void save({ openaiCacheAffinityMaxAgeSeconds: parsedCacheAffinityTtl });
                  }
                }}
                className="h-8 w-28 text-xs"
              />
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs"
                disabled={busy || !cacheAffinityTtlChanged}
                onClick={() => void save({ openaiCacheAffinityMaxAgeSeconds: parsedCacheAffinityTtl })}
              >
                Save TTL
              </Button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
