import { useMemo } from "react";
import { Route } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useModels } from "@/features/api-keys/hooks/use-models";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type RoutingSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

const FORCE_REASONING_OPTIONS = [
  { value: "low", label: "low" },
  { value: "normal", label: "normal" },
  { value: "high", label: "high" },
  { value: "xhigh", label: "xhigh" },
] as const;

export function RoutingSettings({ settings, busy, onSave }: RoutingSettingsProps) {
  const { data: models = [], isLoading: modelsLoading } = useModels();

  const modelOptions = useMemo(() => {
    const byId = new Map(models.map((item) => [item.id, item]));
    if (settings.globalModelForceModel && !byId.has(settings.globalModelForceModel)) {
      byId.set(settings.globalModelForceModel, {
        id: settings.globalModelForceModel,
        name: settings.globalModelForceModel,
      });
    }
    return [...byId.values()].sort((a, b) => a.id.localeCompare(b.id));
  }, [models, settings.globalModelForceModel]);

  const save = (patch: Partial<SettingsUpdateRequest>) =>
    void onSave({
      stickyThreadsEnabled: settings.stickyThreadsEnabled,
      preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
      routingStrategy: settings.routingStrategy,
      globalModelForceEnabled: settings.globalModelForceEnabled,
      globalModelForceModel: settings.globalModelForceModel,
      globalModelForceReasoningEffort: settings.globalModelForceReasoningEffort,
      totpRequiredOnLogin: settings.totpRequiredOnLogin,
      apiKeyAuthEnabled: settings.apiKeyAuthEnabled,
      ...patch,
    });

  const forceModelValue = settings.globalModelForceModel ?? "";
  const forceEffortValue = (settings.globalModelForceReasoningEffort === "medium" ? "normal" : settings.globalModelForceReasoningEffort) ?? "normal";

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

          <div className="space-y-3 p-3">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-sm font-medium">Force all requests</p>
                <p className="text-xs text-muted-foreground">Ignore per-app rules and route everything to one model.</p>
              </div>
              <Switch
                checked={settings.globalModelForceEnabled}
                disabled={busy}
                onCheckedChange={(checked) => {
                  if (!checked) {
                    save({ globalModelForceEnabled: false });
                    return;
                  }
                  const fallbackModel = forceModelValue || modelOptions[0]?.id || "gpt-5.3-codex";
                  save({
                    globalModelForceEnabled: true,
                    globalModelForceModel: fallbackModel,
                    globalModelForceReasoningEffort: forceEffortValue,
                  });
                }}
              />
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <p className="text-xs text-muted-foreground">Forced model</p>
                <Select
                  value={forceModelValue}
                  onValueChange={(value) => save({ globalModelForceModel: value })}
                  disabled={busy || !settings.globalModelForceEnabled || modelOptions.length === 0 || modelsLoading}
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue placeholder={modelsLoading ? "Loading models..." : "Select model"} />
                  </SelectTrigger>
                  <SelectContent>
                    {modelOptions.map((model) => (
                      <SelectItem key={model.id} value={model.id}>
                        {model.id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <p className="text-xs text-muted-foreground">Reasoning effort</p>
                <Select
                  value={forceEffortValue}
                  onValueChange={(value) =>
                    save({ globalModelForceReasoningEffort: value as "low" | "normal" | "high" | "xhigh" })
                  }
                  disabled={busy || !settings.globalModelForceEnabled}
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {FORCE_REASONING_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
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
