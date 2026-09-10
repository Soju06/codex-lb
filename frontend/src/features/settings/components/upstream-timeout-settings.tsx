import { useState } from "react";
import { Timer } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InheritBadge } from "@/features/settings/components/inherit-badge";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export type UpstreamTimeoutSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

// Update-request fields of this card (camelCase) with their backend setting
// name (the `provenance` key). Order is the display order.
const TIMEOUT_FIELDS = [
  { field: "upstreamConnectTimeoutSeconds", name: "upstream_connect_timeout_seconds", key: "connect" },
  { field: "proxyRequestBudgetSeconds", name: "proxy_request_budget_seconds", key: "proxyBudget" },
  { field: "compactRequestBudgetSeconds", name: "compact_request_budget_seconds", key: "compactBudget" },
  {
    field: "transcriptionRequestBudgetSeconds",
    name: "transcription_request_budget_seconds",
    key: "transcriptionBudget",
  },
  // M1 stream/bridge budgets
  {
    field: "httpResponsesStreamRequestBudgetSeconds",
    name: "http_responses_stream_request_budget_seconds",
    key: "streamBudget",
  },
  {
    field: "httpResponsesSessionBridgeRequestBudgetSeconds",
    name: "http_responses_session_bridge_request_budget_seconds",
    key: "bridgeBudget",
  },
  // end M1 stream/bridge budgets
  { field: "streamIdleTimeoutSeconds", name: "stream_idle_timeout_seconds", key: "streamIdle" },
  {
    field: "proxyDownstreamWebsocketIdleTimeoutSeconds",
    name: "proxy_downstream_websocket_idle_timeout_seconds",
    key: "websocketIdle",
  },
  { field: "sseKeepaliveIntervalSeconds", name: "sse_keepalive_interval_seconds", key: "keepalive" },
] as const;

type TimeoutField = (typeof TIMEOUT_FIELDS)[number]["field"];
type TimeoutKey = (typeof TIMEOUT_FIELDS)[number]["key"];

// Mirrors app/core/timeout_invariants.py: the connect timeout must fit inside
// every request budget it is clamped to.
const CONNECT_BUDGET_FIELDS: readonly TimeoutField[] = [
  "proxyRequestBudgetSeconds",
  "compactRequestBudgetSeconds",
  "transcriptionRequestBudgetSeconds",
  "httpResponsesStreamRequestBudgetSeconds",
  "httpResponsesSessionBridgeRequestBudgetSeconds",
];
// Mirrors bridge-stuck-gate-retire-within-bridge-budget: twice the fixed 300 s
// stuck-gate threshold (HTTP_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS) must stay
// strictly below the session bridge request budget.
const BRIDGE_BUDGET_MIN_EXCLUSIVE_SECONDS = 600;
// Mirrors admission-wait-within-stream-budget: the fixed admission wait
// (ADMISSION_WAIT_TIMEOUT_SECONDS) must fit inside the stream request budget.
const ADMISSION_WAIT_SECONDS = 10;
const MAX_SECONDS = 86400;
// Keepalive 0 disables the frames; every other value must be positive.
const ZERO_ALLOWED: ReadonlySet<TimeoutField> = new Set(["sseKeepaliveIntervalSeconds"]);

type Draft = Record<TimeoutField, string>;
type Parsed = { valid: true; value: number | null } | { valid: false };

function isDashboardOwned(settings: DashboardSettings, name: string): boolean {
  return settings.provenance?.[name]?.source === "dashboard";
}

function initialDraft(settings: DashboardSettings): Draft {
  // Only a dashboard-owned value pre-fills its input; an inherited one stays
  // empty and is described by the badge (and placeholder) instead.
  const draft = {} as Draft;
  for (const { field, name } of TIMEOUT_FIELDS) {
    draft[field] = isDashboardOwned(settings, name) ? String(settings[field]) : "";
  }
  return draft;
}

function parseSeconds(raw: string, field: TimeoutField): Parsed {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return { valid: true, value: null };
  }
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed > MAX_SECONDS) {
    return { valid: false };
  }
  if (parsed < 0 || (parsed === 0 && !ZERO_ALLOWED.has(field))) {
    return { valid: false };
  }
  return { valid: true, value: parsed };
}

function inheritedValue(settings: DashboardSettings, field: TimeoutField, name: string): number {
  // What applies once the dashboard value is cleared: env, else code default.
  const provenance = settings.provenance?.[name];
  const candidate = provenance?.envValue ?? provenance?.default;
  return typeof candidate === "number" ? candidate : settings[field];
}

export function UpstreamTimeoutSettings({ settings, busy, onSave }: UpstreamTimeoutSettingsProps) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<Draft>(() => initialDraft(settings));

  const parsed = Object.fromEntries(
    TIMEOUT_FIELDS.map(({ field }) => [field, parseSeconds(draft[field], field)]),
  ) as Record<TimeoutField, Parsed>;

  // Effective value after save: typed value, else what clearing inherits.
  const effective = (field: TimeoutField, name: string): number => {
    const entry = parsed[field];
    return entry.valid && entry.value !== null ? entry.value : inheritedValue(settings, field, name);
  };

  const patch: Partial<SettingsUpdateRequest> = {};
  for (const { field, name } of TIMEOUT_FIELDS) {
    const entry = parsed[field];
    if (!entry.valid) {
      continue;
    }
    const owned = isDashboardOwned(settings, name);
    if (entry.value === null) {
      if (owned) {
        patch[field] = null;
      }
    } else if (!owned || entry.value !== settings[field]) {
      patch[field] = entry.value;
    }
  }

  const invalidKeys = TIMEOUT_FIELDS.filter(({ field }) => !parsed[field].valid).map(({ key }) => key);
  const connect = effective("upstreamConnectTimeoutSeconds", "upstream_connect_timeout_seconds");
  // Like the backend, only violations this edit introduces block the save: a
  // combination already violated by the inherited values must not stop the
  // operator from changing (or fixing) other fields.
  const budgetViolations = TIMEOUT_FIELDS.filter(
    ({ field, name }) =>
      CONNECT_BUDGET_FIELDS.includes(field) &&
      connect > effective(field, name) &&
      !(settings.upstreamConnectTimeoutSeconds > settings[field]),
  );
  const bridgeBudget = effective(
    "httpResponsesSessionBridgeRequestBudgetSeconds",
    "http_responses_session_bridge_request_budget_seconds",
  );
  const bridgeBudgetTooLow =
    bridgeBudget <= BRIDGE_BUDGET_MIN_EXCLUSIVE_SECONDS &&
    !(settings.httpResponsesSessionBridgeRequestBudgetSeconds <= BRIDGE_BUDGET_MIN_EXCLUSIVE_SECONDS);
  const streamBudget = effective(
    "httpResponsesStreamRequestBudgetSeconds",
    "http_responses_stream_request_budget_seconds",
  );
  const streamBudgetBelowAdmissionWait =
    streamBudget < ADMISSION_WAIT_SECONDS &&
    !(settings.httpResponsesStreamRequestBudgetSeconds < ADMISSION_WAIT_SECONDS);
  const changed = Object.keys(patch).length > 0;
  const canSave =
    changed &&
    invalidKeys.length === 0 &&
    budgetViolations.length === 0 &&
    !bridgeBudgetTooLow &&
    !streamBudgetBelowAdmissionWait;

  const save = () => void onSave(buildSettingsUpdateRequest(settings, patch));

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <Timer className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">{t("settings.upstreamTimeouts.title")}</h3>
            <p className="text-xs text-muted-foreground">{t("settings.upstreamTimeouts.description")}</p>
          </div>
        </div>

        <div className="divide-y rounded-lg border">
          {TIMEOUT_FIELDS.map(({ field, name, key }) => (
            <div key={field} className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="space-y-1">
                <p className="text-sm font-medium">{t(`settings.upstreamTimeouts.${key}.label`)}</p>
                <p className="text-xs text-muted-foreground">{t(`settings.upstreamTimeouts.${key}.description`)}</p>
                <InheritBadge settings={settings} name={name} field={field} busy={busy} onSave={onSave} />
              </div>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  min={ZERO_ALLOWED.has(field) ? 0 : undefined}
                  step="any"
                  inputMode="decimal"
                  value={draft[field]}
                  disabled={busy}
                  placeholder={String(settings[field])}
                  onChange={(event) => setDraft((current) => ({ ...current, [field]: event.target.value }))}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && canSave) {
                      save();
                    }
                  }}
                  className="h-8 w-28 text-xs"
                  aria-label={t(`settings.upstreamTimeouts.${key}.label`)}
                />
                <span className="text-xs text-muted-foreground">{t("settings.upstreamTimeouts.secondsSuffix")}</span>
              </div>
            </div>
          ))}
        </div>

        {invalidKeys.map((key: TimeoutKey) => (
          <div
            key={key}
            className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive"
          >
            {t(key === "keepalive" ? "settings.upstreamTimeouts.invalidNonNegative" : "settings.upstreamTimeouts.invalidPositive", {
              field: t(`settings.upstreamTimeouts.${key}.label`),
            })}
          </div>
        ))}
        {budgetViolations.map(({ key }) => (
          <div
            key={key}
            className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive"
          >
            {t("settings.upstreamTimeouts.connectExceedsBudget", {
              connect,
              budget: t(`settings.upstreamTimeouts.${key}.label`),
            })}
          </div>
        ))}
        {streamBudgetBelowAdmissionWait ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive">
            {t("settings.upstreamTimeouts.streamBudgetBelowAdmissionWait", {
              budget: streamBudget,
              minimum: ADMISSION_WAIT_SECONDS,
            })}
          </div>
        ) : null}
        {bridgeBudgetTooLow ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive">
            {t("settings.upstreamTimeouts.bridgeBudgetTooLow", {
              budget: bridgeBudget,
              minimum: BRIDGE_BUDGET_MIN_EXCLUSIVE_SECONDS,
            })}
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
            {t("settings.upstreamTimeouts.save")}
          </Button>
        </div>
      </div>
    </section>
  );
}
