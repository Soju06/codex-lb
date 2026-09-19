import { useState } from "react";
import { DatabaseZap } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InheritBadge } from "@/features/settings/components/inherit-badge";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type DataRetentionSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

const MAX_RETENTION_DAYS = 3650;
const REQUEST_LOG_FLOOR_DAYS = 30;
const USAGE_HISTORY_FLOOR_DAYS = 45;
// Whole numbers only, for the two day windows.
const INTEGER_PATTERN = /^\d+$/;
// The spool window is seconds and float-typed on the wire.
const DECIMAL_SECONDS_PATTERN = /^\d+(\.\d+)?$/;
const REQUEST_LOG_PRESET_DAYS = [30, 90] as const;
// R2 spool retention: seconds, matching the backend field. 3650 days, the same
// ceiling as the two day-based windows above.
const MAX_SPOOL_RETENTION_SECONDS = 315360000;

type ParsedOverride =
  | { valid: true; value: number | null } // null = not configured (cleared input)
  | { valid: false };

function parseOverride(raw: string, floor: number): ParsedOverride {
  const trimmed = raw.trim();
  if (trimmed === "") {
    // Empty input = no dashboard override (not configured = retention disabled).
    return { valid: true, value: null };
  }
  if (!INTEGER_PATTERN.test(trimmed)) {
    return { valid: false };
  }
  const parsed = Number.parseInt(trimmed, 10);
  if (!Number.isFinite(parsed) || parsed > MAX_RETENTION_DAYS) {
    return { valid: false };
  }
  // 0 = disabled; non-zero values have a safety floor so in-product consumer
  // windows stay inside retained data (mirrors the backend validators).
  if (parsed !== 0 && parsed < floor) {
    return { valid: false };
  }
  return { valid: true, value: parsed };
}

function overrideToInput(override: number | null): string {
  return override === null ? "" : String(override);
}

type ParsedSpoolRetention =
  | { valid: true; value: number | null } // null = inherit (empty input)
  | { valid: false };

/**
 * R2 spool retention: mirrors the backend floor check before the PUT. The spool
 * is replayed for as long as a bridge session stays reusable, so a shorter
 * window would delete transcripts a recovery still needs.
 *
 * Seconds are a float on the wire, so a value an API client stored (`7200.5`)
 * must round-trip through this field rather than read as invalid.
 */
function parseSpoolRetention(raw: string, floorSeconds: number): ParsedSpoolRetention {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return { valid: true, value: null };
  }
  if (!DECIMAL_SECONDS_PATTERN.test(trimmed)) {
    return { valid: false };
  }
  const parsed = Number.parseFloat(trimmed);
  if (!Number.isFinite(parsed) || parsed <= 0 || parsed > MAX_SPOOL_RETENTION_SECONDS) {
    return { valid: false };
  }
  if (parsed < floorSeconds) {
    return { valid: false };
  }
  return { valid: true, value: parsed };
}

/** The stored value, or empty while the setting is still inherited. */
function spoolRetentionToInput(settings: DashboardSettings): string {
  return settings.provenance?.http_responses_session_bridge_operation_spool_retention_seconds?.source === "dashboard"
    ? String(settings.httpResponsesSessionBridgeOperationSpoolRetentionSeconds)
    : "";
}

export function DataRetentionSettings({ settings, busy, onSave }: DataRetentionSettingsProps) {
  const { t } = useTranslation();
  const [requestLogDays, setRequestLogDays] = useState(overrideToInput(settings.requestLogRetentionOverrideDays));
  const [usageHistoryDays, setUsageHistoryDays] = useState(
    overrideToInput(settings.usageHistoryRetentionOverrideDays),
  );
  const [spoolRetentionSeconds, setSpoolRetentionSeconds] = useState(() => spoolRetentionToInput(settings));
  const spoolFloorSeconds = settings.httpResponsesSessionBridgeOperationSpoolRetentionFloorSeconds;

  const parsedRequestLog = parseOverride(requestLogDays, REQUEST_LOG_FLOOR_DAYS);
  const parsedUsageHistory = parseOverride(usageHistoryDays, USAGE_HISTORY_FLOOR_DAYS);
  const requestLogChanged =
    parsedRequestLog.valid && parsedRequestLog.value !== settings.requestLogRetentionOverrideDays;
  const usageHistoryChanged =
    parsedUsageHistory.valid && parsedUsageHistory.value !== settings.usageHistoryRetentionOverrideDays;
  const parsedSpoolRetention = parseSpoolRetention(spoolRetentionSeconds, spoolFloorSeconds);
  const spoolRetentionEdited = spoolRetentionSeconds.trim() !== spoolRetentionToInput(settings);
  const spoolRetentionChanged = parsedSpoolRetention.valid && spoolRetentionEdited;
  // An untouched spool field never blocks the other two windows: a stored value
  // the API accepts but this card cannot represent must not make the card
  // read-only. Only an edit the backend would reject blocks saving.
  const spoolRetentionRejected = spoolRetentionEdited && !parsedSpoolRetention.valid;
  const canSave =
    parsedRequestLog.valid &&
    parsedUsageHistory.valid &&
    !spoolRetentionRejected &&
    (requestLogChanged || usageHistoryChanged || spoolRetentionChanged);

  const save = () => {
    // Only submit this card's edited fields: a value stores an override, null
    // clears it (back to not configured = disabled), and untouched
    // fields stay out of the payload entirely.
    const patch: Partial<SettingsUpdateRequest> = {};
    if (requestLogChanged && parsedRequestLog.valid) {
      patch.requestLogRetentionOverrideDays = parsedRequestLog.value;
    }
    if (usageHistoryChanged && parsedUsageHistory.valid) {
      patch.usageHistoryRetentionOverrideDays = parsedUsageHistory.value;
    }
    if (spoolRetentionChanged && parsedSpoolRetention.valid) {
      patch.httpResponsesSessionBridgeOperationSpoolRetentionSeconds = parsedSpoolRetention.value;
    }
    void onSave(buildSettingsUpdateRequest(settings, patch));
  };

  const showRequestLogDisabledInfo = settings.requestLogRetentionDays === 0;

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <DatabaseZap className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">{t("settings.retention.title")}</h3>
              <p className="text-xs text-muted-foreground">{t("settings.retention.description")}</p>
            </div>
          </div>
        </div>

        {showRequestLogDisabledInfo ? (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">
              {t("settings.retention.requestLogs.disabledInfo")}
            </p>
            <div
              className="flex flex-wrap items-center gap-2"
              role="group"
              aria-label={t("settings.retention.requestLogs.presetsLabel")}
            >
              <span className="text-xs font-medium text-muted-foreground">
                {t("settings.retention.requestLogs.presetsLabel")}
              </span>
              {REQUEST_LOG_PRESET_DAYS.map((days) => {
                const selected = requestLogDays === String(days);
                return (
                  <Button
                    key={days}
                    type="button"
                    size="sm"
                    variant={selected ? "default" : "outline"}
                    className="h-7 px-2.5 text-xs"
                    disabled={busy}
                    aria-pressed={selected}
                    onClick={() => setRequestLogDays(String(days))}
                  >
                    {t("settings.retention.requestLogs.presetButton", { days })}
                  </Button>
                );
              })}
            </div>
          </div>
        ) : null}

        <div className="divide-y rounded-lg border">
          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="space-y-1">
              <p className="text-sm font-medium">{t("settings.retention.requestLogs.label")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.retention.requestLogs.description")}</p>
              <InheritBadge
                settings={settings}
                name="request_log_retention_days"
                field="requestLogRetentionOverrideDays"
                busy={busy}
                onSave={onSave}
              />
            </div>
            <div className="flex items-center gap-2">
              <Input
                type="number"
                min={0}
                max={MAX_RETENTION_DAYS}
                step={1}
                inputMode="numeric"
                value={requestLogDays}
                disabled={busy}
                placeholder={t("settings.retention.inheritPlaceholder")}
                onChange={(event) => setRequestLogDays(event.target.value)}
                className="h-8 w-24 text-xs"
                aria-label={t("settings.retention.requestLogs.ariaLabel")}
              />
              <span className="text-xs text-muted-foreground">{t("settings.retention.daysSuffix")}</span>
            </div>
          </div>
          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="space-y-1">
              <p className="text-sm font-medium">{t("settings.retention.usageHistory.label")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.retention.usageHistory.description")}</p>
              <InheritBadge
                settings={settings}
                name="usage_history_retention_days"
                field="usageHistoryRetentionOverrideDays"
                busy={busy}
                onSave={onSave}
              />
            </div>
            <div className="flex items-center gap-2">
              <Input
                type="number"
                min={0}
                max={MAX_RETENTION_DAYS}
                step={1}
                inputMode="numeric"
                value={usageHistoryDays}
                disabled={busy}
                placeholder={t("settings.retention.inheritPlaceholder")}
                onChange={(event) => setUsageHistoryDays(event.target.value)}
                className="h-8 w-24 text-xs"
                aria-label={t("settings.retention.usageHistory.ariaLabel")}
              />
              <span className="text-xs text-muted-foreground">{t("settings.retention.daysSuffix")}</span>
            </div>
          </div>
          {/* R2 spool retention: the durable HTTP bridge operation spool holds
              raw request payloads, so its window belongs on this card. */}
          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="space-y-1">
              <p className="text-sm font-medium">{t("settings.retention.spool.label")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.retention.spool.description")}</p>
              <p className="text-xs text-muted-foreground">
                {t("settings.retention.spool.floorHint", { seconds: spoolFloorSeconds })}
              </p>
              <InheritBadge
                settings={settings}
                name="http_responses_session_bridge_operation_spool_retention_seconds"
                field="httpResponsesSessionBridgeOperationSpoolRetentionSeconds"
                busy={busy}
                onSave={onSave}
              />
            </div>
            <div className="flex items-center gap-2">
              <Input
                type="number"
                min={spoolFloorSeconds}
                max={MAX_SPOOL_RETENTION_SECONDS}
                step="any"
                inputMode="decimal"
                value={spoolRetentionSeconds}
                disabled={busy}
                placeholder={t("settings.retention.inheritPlaceholder")}
                onChange={(event) => setSpoolRetentionSeconds(event.target.value)}
                className="h-8 w-28 text-xs"
                aria-label={t("settings.retention.spool.ariaLabel")}
              />
              <span className="text-xs text-muted-foreground">{t("settings.retention.secondsSuffix")}</span>
            </div>
          </div>
        </div>

        {!parsedRequestLog.valid ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive">
            {t("settings.retention.requestLogs.invalid")}
          </div>
        ) : null}
        {!parsedUsageHistory.valid ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive">
            {t("settings.retention.usageHistory.invalid")}
          </div>
        ) : null}
        {spoolRetentionRejected ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive">
            {t("settings.retention.spool.invalid", { seconds: spoolFloorSeconds })}
          </div>
        ) : null}

        <div className="flex justify-end">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 text-xs"
            disabled={busy || !canSave}
            onClick={save}
          >
            {t("settings.retention.save")}
          </Button>
        </div>
      </div>
    </section>
  );
}
