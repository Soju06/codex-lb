import { Activity, CalendarClock } from "lucide-react";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SpinnerBlock } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { useQuotaPlanner } from "@/features/quota-planner/hooks/use-quota-planner";
import type {
  QuotaPlannerForecastQuantile,
  QuotaPlannerMode,
  QuotaPlannerSettings,
} from "@/features/quota-planner/schemas";
import { getErrorMessageOrNull } from "@/utils/errors";
import { formatTimeLong } from "@/utils/formatters";

const WEEKDAYS = [
  { value: 0, label: "Mon" },
  { value: 1, label: "Tue" },
  { value: 2, label: "Wed" },
  { value: 3, label: "Thu" },
  { value: 4, label: "Fri" },
  { value: 5, label: "Sat" },
  { value: 6, label: "Sun" },
];

function formatNumber(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function formatInlineTime(value: string): string {
  const formatted = formatTimeLong(value);
  return `${formatted.date} ${formatted.time}`;
}

function detailValue(details: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = details?.[key];
  if (value === null || value === undefined || value === "") {
    return null;
  }
  if (typeof value === "number") {
    return formatNumber(value);
  }
  return String(value);
}

function settingsDraft(settings: QuotaPlannerSettings): QuotaPlannerSettings {
  return { ...settings, workingDays: [...settings.workingDays] };
}

export function QuotaPlannerSection() {
  const {
    settingsQuery,
    decisionsQuery,
    forecastQuery,
    updateSettingsMutation,
    warmNowMutation,
    cancelDecisionMutation,
  } = useQuotaPlanner();
  const settings = settingsQuery.data;
  const [draft, setDraft] = useState<QuotaPlannerSettings | null>(null);
  const [warmAccountId, setWarmAccountId] = useState("");
  const [forceWarmupProbe, setForceWarmupProbe] = useState(false);
  const effectiveDraft = draft ?? (settings ? settingsDraft(settings) : null);

  const error = useMemo(
    () =>
      getErrorMessageOrNull(settingsQuery.error) ||
      getErrorMessageOrNull(decisionsQuery.error) ||
      getErrorMessageOrNull(forecastQuery.error) ||
      getErrorMessageOrNull(updateSettingsMutation.error) ||
      getErrorMessageOrNull(warmNowMutation.error) ||
      getErrorMessageOrNull(cancelDecisionMutation.error),
    [
      settingsQuery.error,
      decisionsQuery.error,
      forecastQuery.error,
      updateSettingsMutation.error,
      warmNowMutation.error,
      cancelDecisionMutation.error,
    ],
  );

  const settingsBusy = updateSettingsMutation.isPending;
  const warmupBusy = warmNowMutation.isPending;
  const cancelBusy = cancelDecisionMutation.isPending;
  const changed = settings && effectiveDraft ? JSON.stringify(settings) !== JSON.stringify(effectiveDraft) : false;
  const forecast = forecastQuery.data;
  const decisions = decisionsQuery.data ?? [];

  const patchDraft = (patch: Partial<QuotaPlannerSettings>) => {
    setDraft((current) => {
      const base = current ?? (settings ? settingsDraft(settings) : null);
      return base ? { ...base, ...patch } : null;
    });
  };

  const toggleWorkingDay = (day: number, checked: boolean) => {
    setDraft((current) => {
      const base = current ?? (settings ? settingsDraft(settings) : null);
      if (!base) {
        return null;
      }
      const nextDays = checked
        ? [...new Set([...base.workingDays, day])].sort()
        : base.workingDays.filter((value) => value !== day);
      return { ...base, workingDays: nextDays.length > 0 ? nextDays : base.workingDays };
    });
  };

  return (
    <section className="space-y-3 rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <CalendarClock className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">Quota phase planner</h3>
          <p className="text-xs text-muted-foreground">
            Preserve cold windows, prefer expiring capacity, and record warmup decisions in safe shadow mode by default.
          </p>
        </div>
      </div>

      {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

      {!effectiveDraft ? (
        <div className="py-8">
          <SpinnerBlock />
        </div>
      ) : (
        <>
          <div className="grid gap-3 lg:grid-cols-[1.2fr_0.8fr]">
            <div className="divide-y rounded-lg border">
              <div className="grid gap-3 p-3 sm:grid-cols-3">
                <label className="space-y-1 text-xs font-medium">
                  Mode
                  <Select
                    value={effectiveDraft.mode}
                    onValueChange={(value) => patchDraft({ mode: value as QuotaPlannerMode })}
                  >
                    <SelectTrigger className="h-8 text-xs" disabled={settingsBusy}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="shadow">Shadow</SelectItem>
                      <SelectItem value="suggest">Suggest</SelectItem>
                      <SelectItem value="auto">Auto</SelectItem>
                      <SelectItem value="off">Off</SelectItem>
                    </SelectContent>
                  </Select>
                </label>
                <label className="space-y-1 text-xs font-medium">
                  Timezone
                  <Input
                    className="h-8 text-xs"
                    value={effectiveDraft.timezone}
                    disabled={settingsBusy}
                    onChange={(event) => patchDraft({ timezone: event.target.value })}
                  />
                </label>
                <label className="space-y-1 text-xs font-medium">
                  Forecast
                  <Select
                    value={effectiveDraft.forecastQuantile}
                    onValueChange={(value) =>
                      patchDraft({ forecastQuantile: value as QuotaPlannerForecastQuantile })
                    }
                  >
                    <SelectTrigger className="h-8 text-xs" disabled={settingsBusy}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="p50">P50</SelectItem>
                      <SelectItem value="p75">P75</SelectItem>
                      <SelectItem value="p90">P90</SelectItem>
                    </SelectContent>
                  </Select>
                </label>
              </div>

              <div className="grid gap-3 p-3 sm:grid-cols-4">
                <label className="space-y-1 text-xs font-medium">
                  Work start
                  <Input
                    className="h-8 text-xs"
                    type="time"
                    value={effectiveDraft.workingHoursStart}
                    disabled={settingsBusy}
                    onChange={(event) => patchDraft({ workingHoursStart: event.target.value })}
                  />
                </label>
                <label className="space-y-1 text-xs font-medium">
                  Work end
                  <Input
                    className="h-8 text-xs"
                    type="time"
                    value={effectiveDraft.workingHoursEnd}
                    disabled={settingsBusy}
                    onChange={(event) => patchDraft({ workingHoursEnd: event.target.value })}
                  />
                </label>
                <label className="space-y-1 text-xs font-medium">
                  Lead minutes
                  <Input
                    className="h-8 text-xs"
                    type="number"
                    min={0}
                    max={1440}
                    value={effectiveDraft.prewarmLeadMinutes}
                    disabled={settingsBusy}
                    onChange={(event) =>
                      patchDraft({ prewarmLeadMinutes: Number.parseInt(event.target.value || "0", 10) })
                    }
                  />
                </label>
                <label className="space-y-1 text-xs font-medium">
                  Max decisions/day
                  <Input
                    className="h-8 text-xs"
                    type="number"
                    min={0}
                    value={effectiveDraft.maxWarmupsPerDay}
                    disabled={settingsBusy}
                    onChange={(event) =>
                      patchDraft({ maxWarmupsPerDay: Number.parseInt(event.target.value || "0", 10) })
                    }
                  />
                </label>
              </div>

              <div className="flex flex-wrap gap-2 p-3">
                {WEEKDAYS.map((day) => (
                  <label
                    key={day.value}
                    className="flex h-8 items-center gap-2 rounded-md border px-2 text-xs font-medium"
                  >
                    <Checkbox
                      checked={effectiveDraft.workingDays.includes(day.value)}
                      disabled={settingsBusy}
                      onCheckedChange={(checked) => toggleWorkingDay(day.value, checked === true)}
                    />
                    {day.label}
                  </label>
                ))}
              </div>

              <div className="grid gap-3 p-3 sm:grid-cols-3">
                <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
                  <span className="text-xs font-medium">Prewarm planning</span>
                  <Switch
                    checked={effectiveDraft.prewarmEnabled}
                    disabled={settingsBusy}
                    onCheckedChange={(checked) => patchDraft({ prewarmEnabled: checked })}
                  />
                </div>
                <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
                  <span className="text-xs font-medium">Synthetic traffic</span>
                  <Switch
                    checked={effectiveDraft.allowSyntheticTraffic}
                    disabled={settingsBusy}
                    onCheckedChange={(checked) => patchDraft({ allowSyntheticTraffic: checked })}
                  />
                </div>
                <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
                  <span className="text-xs font-medium">Dry run</span>
                  <Switch
                    checked={effectiveDraft.dryRun}
                    disabled={settingsBusy}
                    onCheckedChange={(checked) => patchDraft({ dryRun: checked })}
                  />
                </div>
              </div>

              <div className="grid gap-3 p-3 sm:grid-cols-3">
                <label className="space-y-1 text-xs font-medium">
                  Min expected gain
                  <Input
                    className="h-8 text-xs"
                    type="number"
                    min={0}
                    step="0.1"
                    value={effectiveDraft.minExpectedGain}
                    disabled={settingsBusy}
                    onChange={(event) => patchDraft({ minExpectedGain: Number.parseFloat(event.target.value || "0") })}
                  />
                </label>
                <label className="space-y-1 text-xs font-medium">
                  Daily warmup credits
                  <Input
                    className="h-8 text-xs"
                    type="number"
                    min={0}
                    step="0.1"
                    value={effectiveDraft.maxWarmupCreditsPerDay}
                    disabled={settingsBusy}
                    onChange={(event) =>
                      patchDraft({ maxWarmupCreditsPerDay: Number.parseFloat(event.target.value || "0") })
                    }
                  />
                </label>
                <label className="space-y-1 text-xs font-medium">
                  Warmup model
                  <Input
                    className="h-8 text-xs"
                    value={effectiveDraft.warmupModelPreference ?? ""}
                    disabled={settingsBusy}
                    onChange={(event) =>
                      patchDraft({ warmupModelPreference: event.target.value.trim() || null })
                    }
                  />
                </label>
              </div>
            </div>

            <div className="space-y-3 rounded-lg border p-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Activity className="h-4 w-4 text-primary" aria-hidden="true" />
                  <span className="text-sm font-semibold">Forecast</span>
                </div>
                <Badge variant={effectiveDraft.mode === "off" ? "secondary" : "default"}>{effectiveDraft.mode}</Badge>
              </div>
              {forecastQuery.isLoading && !forecast ? (
                <SpinnerBlock />
              ) : forecast ? (
                <div className="grid gap-2 text-xs">
                  <div className="flex justify-between gap-3">
                    <span className="text-muted-foreground">Demand</span>
                    <span className="font-medium tabular-nums">{formatNumber(forecast.totalDemandUnits)}</span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span className="text-muted-foreground">Served in simulation</span>
                    <span className="font-medium tabular-nums">{formatNumber(forecast.simulation.servedUnits)}</span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span className="text-muted-foreground">Unmet</span>
                    <span className="font-medium tabular-nums">{formatNumber(forecast.simulation.unmetDemand)}</span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span className="text-muted-foreground">Peak</span>
                    <span className="font-medium tabular-nums">{formatNumber(forecast.peakDemandUnits)}</span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span className="text-muted-foreground">Peak slot</span>
                    <span className="text-right font-medium">
                      {forecast.peakSlotStart ? formatInlineTime(forecast.peakSlotStart) : "No demand yet"}
                    </span>
                  </div>
                </div>
              ) : null}
            </div>
          </div>

          <div className="flex items-center justify-end gap-2">
            <Input
              aria-label="Warm account id"
              className="h-8 w-48 text-xs"
              placeholder="account id"
              value={warmAccountId}
              disabled={warmupBusy}
              onChange={(event) => setWarmAccountId(event.target.value)}
            />
            <label className="flex h-8 items-center gap-2 rounded-md border px-2 text-xs font-medium">
              <Checkbox
                checked={forceWarmupProbe}
                disabled={warmupBusy}
                onCheckedChange={(checked) => setForceWarmupProbe(checked === true)}
              />
              Force probe
            </label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              disabled={warmupBusy || warmAccountId.trim().length === 0}
              onClick={() => {
                warmNowMutation.mutate({
                  accountId: warmAccountId.trim(),
                  model: effectiveDraft.warmupModelPreference,
                  forceProbe: forceWarmupProbe,
                });
              }}
            >
              Warm Now
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              disabled={settingsBusy || !changed || !settings}
              onClick={() => setDraft(null)}
            >
              Reset
            </Button>
            <Button
              type="button"
              size="sm"
              className="h-8 text-xs"
              disabled={settingsBusy || !changed}
              onClick={() => {
                if (!effectiveDraft) {
                  return;
                }
                updateSettingsMutation.mutate(effectiveDraft, { onSuccess: () => setDraft(null) });
              }}
            >
              Save Planner
            </Button>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-semibold uppercase text-muted-foreground">Recent decisions</h4>
              <span className="text-xs text-muted-foreground">{decisions.length} shown</span>
            </div>
            <div className="overflow-hidden rounded-lg border">
              {decisions.length === 0 ? (
                <div className="px-3 py-5 text-center text-xs text-muted-foreground">
                  Planner decisions appear after the next scheduler tick.
                </div>
              ) : (
                <div className="divide-y">
                  {decisions.slice(0, 8).map((decision) => {
                    const targetPeak = detailValue(decision.details, "target_peak_at");
                    const warmupCycle = detailValue(decision.details, "warmup_cycle");
                    const expectedGain = detailValue(decision.details, "expected_gain");
                    const expectedCost = detailValue(decision.details, "expected_cost");
                    const skipReason =
                      detailValue(decision.details, "skip_reason") || detailValue(decision.details, "noop_reason");
                    return (
                      <div key={decision.id} className="grid gap-2 px-3 py-2 text-xs sm:grid-cols-[9rem_1fr_5rem_5rem]">
                        <span className="text-muted-foreground">{formatInlineTime(decision.createdAt)}</span>
                        <span className="min-w-0">
                          <span className="block truncate">
                            {decision.action}
                            {decision.accountId ? ` ${decision.accountId}` : ""} · {decision.status}
                            {decision.scheduledAt ? ` · scheduled ${formatInlineTime(decision.scheduledAt)}` : ""}
                          </span>
                          <span className="block truncate text-muted-foreground">
                            {targetPeak ? `peak ${targetPeak}` : decision.reason ?? "no reason"}
                            {expectedGain ? ` · gain ${expectedGain}` : ""}
                            {expectedCost ? ` · cost ${expectedCost}` : ""}
                            {warmupCycle ? ` · ${warmupCycle}` : ""}
                            {skipReason ? ` · ${skipReason}` : ""}
                          </span>
                        </span>
                        <span className="text-right font-medium tabular-nums">{formatNumber(decision.score)}</span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs"
                          disabled={cancelBusy || !["planned", "skipped"].includes(decision.status)}
                          onClick={() => cancelDecisionMutation.mutate(decision.id)}
                        >
                          Cancel
                        </Button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
}
