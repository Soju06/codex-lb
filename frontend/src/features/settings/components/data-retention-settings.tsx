import { useState } from "react";
import { DatabaseZap } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
const INTEGER_DAYS_PATTERN = /^\d+$/;

function parseRetentionDays(raw: string, floor: number): number | null {
  const trimmed = raw.trim();
  if (!INTEGER_DAYS_PATTERN.test(trimmed)) {
    return null;
  }
  const parsed = Number.parseInt(trimmed, 10);
  if (!Number.isFinite(parsed) || parsed > MAX_RETENTION_DAYS) {
    return null;
  }
  // 0 = disabled; non-zero values have a safety floor so in-product consumer
  // windows stay inside retained data (mirrors the backend validators).
  if (parsed !== 0 && parsed < floor) {
    return null;
  }
  return parsed;
}

export function DataRetentionSettings({ settings, busy, onSave }: DataRetentionSettingsProps) {
  const { t } = useTranslation();
  const [requestLogDays, setRequestLogDays] = useState(String(settings.requestLogRetentionDays));
  const [usageHistoryDays, setUsageHistoryDays] = useState(String(settings.usageHistoryRetentionDays));

  const parsedRequestLogDays = parseRetentionDays(requestLogDays, REQUEST_LOG_FLOOR_DAYS);
  const parsedUsageHistoryDays = parseRetentionDays(usageHistoryDays, USAGE_HISTORY_FLOOR_DAYS);
  const requestLogValid = parsedRequestLogDays !== null;
  const usageHistoryValid = parsedUsageHistoryDays !== null;
  const requestLogChanged = requestLogValid && parsedRequestLogDays !== settings.requestLogRetentionDays;
  const usageHistoryChanged = usageHistoryValid && parsedUsageHistoryDays !== settings.usageHistoryRetentionDays;
  const canSave = requestLogValid && usageHistoryValid && (requestLogChanged || usageHistoryChanged);

  const save = () => {
    // Only submit edited fields so an untouched value keeps inheriting the
    // (deprecated) env alias instead of being pinned as a dashboard override.
    const patch: Partial<SettingsUpdateRequest> = {};
    if (requestLogChanged && parsedRequestLogDays !== null) {
      patch.requestLogRetentionDays = parsedRequestLogDays;
    }
    if (usageHistoryChanged && parsedUsageHistoryDays !== null) {
      patch.usageHistoryRetentionDays = parsedUsageHistoryDays;
    }
    void onSave(buildSettingsUpdateRequest(settings, patch));
  };

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

        <div className="divide-y rounded-lg border">
          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">{t("settings.retention.requestLogs.label")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.retention.requestLogs.description")}</p>
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
                onChange={(event) => setRequestLogDays(event.target.value)}
                className="h-8 w-24 text-xs"
                aria-label={t("settings.retention.requestLogs.ariaLabel")}
              />
              <span className="text-xs text-muted-foreground">{t("settings.retention.daysSuffix")}</span>
            </div>
          </div>
          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">{t("settings.retention.usageHistory.label")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.retention.usageHistory.description")}</p>
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
                onChange={(event) => setUsageHistoryDays(event.target.value)}
                className="h-8 w-24 text-xs"
                aria-label={t("settings.retention.usageHistory.ariaLabel")}
              />
              <span className="text-xs text-muted-foreground">{t("settings.retention.daysSuffix")}</span>
            </div>
          </div>
        </div>

        {!requestLogValid && requestLogDays.trim() !== "" ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive">
            {t("settings.retention.requestLogs.invalid")}
          </div>
        ) : null}
        {!usageHistoryValid && usageHistoryDays.trim() !== "" ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive">
            {t("settings.retention.usageHistory.invalid")}
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
