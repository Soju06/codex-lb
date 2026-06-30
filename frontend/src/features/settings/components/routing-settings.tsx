import { useReducer } from "react";
import { Route, Zap } from "lucide-react";
import { useTranslation } from "react-i18next";

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
import { Switch } from "@/components/ui/switch";
import type { AccountSummary } from "@/features/accounts/schemas";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type {
  AdditionalQuotaRoutingPolicy,
  DashboardSettings,
  SettingsUpdateRequest,
} from "@/features/settings/schemas";
import { formatCompactAccountId } from "@/utils/account-identifiers";
import { isSingleAccountRoutingSelectable } from "@/utils/account-status";

const WARMUP_MODEL_MAX_LENGTH = 128;
const LIMIT_WARMUP_MODEL_MAX_LENGTH = 128;
const LIMIT_WARMUP_PROMPT_MAX_LENGTH = 512;
const WEEKDAYS = [
  { value: 0, key: "mon" },
  { value: 1, key: "tue" },
  { value: 2, key: "wed" },
  { value: 3, key: "thu" },
  { value: 4, key: "fri" },
  { value: 5, key: "sat" },
  { value: 6, key: "sun" },
] as const;

function parseWorkingDays(value: string): Set<number> {
  const days = new Set(
    value
      .split(",")
      .map((part) => Number(part.trim()))
      .filter((day) => Number.isInteger(day) && day >= 0 && day <= 6),
  );
  return days.size > 0 ? days : new Set(WEEKDAYS.map((day) => day.value));
}

function serializeWorkingDays(days: Set<number>): string {
  return Array.from(days).toSorted((a, b) => a - b).join(",");
}

export type RoutingSettingsProps = {
  settings: DashboardSettings;
  accounts?: AccountSummary[];
  accountsLoading?: boolean;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

const EMPTY_ACCOUNTS: AccountSummary[] = [];

function accountLabel(account: AccountSummary): string {
  const name = account.alias?.trim() || account.displayName?.trim() || account.email?.trim() || account.accountId;
  const compactId = formatCompactAccountId(account.accountId, 6, 4);
  return `${name} (${compactId})`;
}

type RoutingSettingsDraft = {
  warmupModel: string;
  cacheAffinityTtl: string;
  relativeAvailabilityPower: string;
  relativeAvailabilityTopK: string;
  stickyPrimaryThreshold: string;
  stickySecondaryThreshold: string;
  limitWarmupModel: string;
  limitWarmupPrompt: string;
  limitWarmupCooldown: string;
  additionalQuotaKey: string;
  additionalQuotaPolicy: AdditionalQuotaRoutingPolicy;
};

function createRoutingSettingsDraft(settings: DashboardSettings): RoutingSettingsDraft {
  return {
    warmupModel: settings.warmupModel,
    cacheAffinityTtl: String(settings.openaiCacheAffinityMaxAgeSeconds),
    relativeAvailabilityPower: String(settings.relativeAvailabilityPower),
    relativeAvailabilityTopK: String(settings.relativeAvailabilityTopK),
    stickyPrimaryThreshold: String(settings.stickyReallocationPrimaryBudgetThresholdPct ?? 95),
    stickySecondaryThreshold: String(settings.stickyReallocationSecondaryBudgetThresholdPct ?? 100),
    limitWarmupModel: settings.limitWarmupModel,
    limitWarmupPrompt: settings.limitWarmupPrompt,
    limitWarmupCooldown: String(settings.limitWarmupCooldownSeconds),
    additionalQuotaKey: "",
    additionalQuotaPolicy: "inherit",
  };
}

function routingSettingsDraftReducer(
  state: RoutingSettingsDraft,
  patch: Partial<RoutingSettingsDraft>,
): RoutingSettingsDraft {
  return { ...state, ...patch };
}

export function RoutingSettings({
  settings,
  accounts = EMPTY_ACCOUNTS,
  accountsLoading = false,
  busy,
  onSave,
}: RoutingSettingsProps) {
  const { t } = useTranslation();
  const [draft, updateDraft] = useReducer(
    routingSettingsDraftReducer,
    settings,
    createRoutingSettingsDraft,
  );

  const save = (patch: Partial<SettingsUpdateRequest>) =>
    void onSave(buildSettingsUpdateRequest(settings, patch));
  const saveAdditionalQuotaPolicy = (
    quotaKey: string,
    policy: AdditionalQuotaRoutingPolicy,
  ) => {
    const normalizedKey = quotaKey.trim();
    if (!normalizedKey) {
      return;
    }
    save({
      additionalQuotaRoutingPolicies: {
        ...(settings.additionalQuotaRoutingPolicies ?? {}),
        [normalizedKey]: policy,
      },
    });
  };
  const removeAdditionalQuotaPolicy = (quotaKey: string) => {
    const next = { ...(settings.additionalQuotaRoutingPolicies ?? {}) };
    delete next[quotaKey];
    save({ additionalQuotaRoutingPolicies: next });
  };

  const parsedCacheAffinityTtl = Number.parseInt(draft.cacheAffinityTtl, 10);
  const cacheAffinityTtlValid = Number.isInteger(parsedCacheAffinityTtl) && parsedCacheAffinityTtl > 0;
  const cacheAffinityTtlChanged =
    cacheAffinityTtlValid && parsedCacheAffinityTtl !== settings.openaiCacheAffinityMaxAgeSeconds;
  const warmupModelChanged = draft.warmupModel.trim() !== settings.warmupModel;
  const warmupModelValid = draft.warmupModel.trim().length > 0 && draft.warmupModel.trim().length <= WARMUP_MODEL_MAX_LENGTH;
  const parsedLimitWarmupCooldown = Number(draft.limitWarmupCooldown);
  const limitWarmupCooldownValid = Number.isInteger(parsedLimitWarmupCooldown) && parsedLimitWarmupCooldown >= 60;
  const limitWarmupFieldsChanged =
    draft.limitWarmupModel.trim() !== settings.limitWarmupModel ||
    draft.limitWarmupPrompt.trim() !== settings.limitWarmupPrompt ||
    (limitWarmupCooldownValid && parsedLimitWarmupCooldown !== settings.limitWarmupCooldownSeconds);
  const limitWarmupFieldsValid =
    draft.limitWarmupModel.trim().length > 0 &&
    draft.limitWarmupModel.trim().length <= LIMIT_WARMUP_MODEL_MAX_LENGTH &&
    draft.limitWarmupPrompt.trim().length > 0 &&
    draft.limitWarmupPrompt.trim().length <= LIMIT_WARMUP_PROMPT_MAX_LENGTH &&
    limitWarmupCooldownValid;

  const parsedRelativeAvailabilityPower = Number.parseFloat(draft.relativeAvailabilityPower);
  const relativeAvailabilityPowerValid =
    Number.isFinite(parsedRelativeAvailabilityPower) && parsedRelativeAvailabilityPower > 0;
  const relativeAvailabilityPowerChanged =
    relativeAvailabilityPowerValid && parsedRelativeAvailabilityPower !== settings.relativeAvailabilityPower;

  const relativeAvailabilityTopKTrimmed = draft.relativeAvailabilityTopK.trim();
  const parsedRelativeAvailabilityTopK = Number(relativeAvailabilityTopKTrimmed);
  const relativeAvailabilityTopKValid =
    /^[0-9]+$/.test(relativeAvailabilityTopKTrimmed) &&
    Number.isInteger(parsedRelativeAvailabilityTopK) &&
    parsedRelativeAvailabilityTopK >= 1 &&
    parsedRelativeAvailabilityTopK <= 20;
  const relativeAvailabilityTopKChanged =
    relativeAvailabilityTopKValid && parsedRelativeAvailabilityTopK !== settings.relativeAvailabilityTopK;

  const relativeAvailabilitySelected = settings.routingStrategy === "relative_availability";
  const selectableAccounts = accounts.filter((account) => isSingleAccountRoutingSelectable(account.status));
  const selectedAccount = accounts.find((account) => account.accountId === settings.singleAccountId);
  const blockedSelectedAccount =
    selectedAccount !== undefined && !isSingleAccountRoutingSelectable(selectedAccount.status) ? selectedAccount : null;
  const firstAccountId = selectableAccounts[0]?.accountId;
  const additionalQuotaOverrides = settings.additionalQuotaRoutingPolicies ?? {};
  const knownAdditionalQuotaKeys = new Set((settings.additionalQuotaPolicies ?? []).map((policy) => policy.quotaKey));
  const additionalQuotaRows = [
    ...(settings.additionalQuotaPolicies ?? []).map((policy) => ({
      quotaKey: policy.quotaKey,
      label: policy.displayLabel || policy.quotaKey,
      policy: policy.routingPolicy,
      hasOverride: Object.prototype.hasOwnProperty.call(additionalQuotaOverrides, policy.quotaKey),
    })),
    ...Object.entries(additionalQuotaOverrides).reduce<
      Array<{ quotaKey: string; label: string; policy: AdditionalQuotaRoutingPolicy; hasOverride: boolean }>
    >((rows, [quotaKey, policy]) => {
      if (!knownAdditionalQuotaKeys.has(quotaKey)) {
        rows.push({
          quotaKey,
          label: quotaKey,
          policy,
          hasOverride: true,
        });
      }
      return rows;
    }, []),
  ];
  const parsedStickyPrimaryThreshold = Number.parseFloat(draft.stickyPrimaryThreshold);
  const stickyPrimaryThresholdValid =
    Number.isFinite(parsedStickyPrimaryThreshold) &&
    parsedStickyPrimaryThreshold >= 0 &&
    parsedStickyPrimaryThreshold <= 100;
  const stickyPrimaryThresholdChanged =
    stickyPrimaryThresholdValid &&
    parsedStickyPrimaryThreshold !== (settings.stickyReallocationPrimaryBudgetThresholdPct ?? 95);
  const parsedStickySecondaryThreshold = Number.parseFloat(draft.stickySecondaryThreshold);
  const stickySecondaryThresholdValid =
    Number.isFinite(parsedStickySecondaryThreshold) &&
    parsedStickySecondaryThreshold >= 0 &&
    parsedStickySecondaryThreshold <= 100;
  const stickySecondaryThresholdChanged =
    stickySecondaryThresholdValid &&
    parsedStickySecondaryThreshold !== (settings.stickyReallocationSecondaryBudgetThresholdPct ?? 100);
  const workingDays = parseWorkingDays(settings.weeklyPaceWorkingDays);
  const toggleWorkingDay = (day: number, checked: boolean) => {
    const next = new Set(workingDays);
    if (checked) {
      next.add(day);
    } else if (next.size > 1) {
      next.delete(day);
    }
    save({ weeklyPaceWorkingDays: serializeWorkingDays(next) });
  };

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <Route className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">{t("settings.routing.title")}</h3>
              <p className="text-xs text-muted-foreground">{t("settings.routing.description")}</p>
            </div>
          </div>
        </div>

        <div className="divide-y rounded-lg border">
          <div className="space-y-3 p-3">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-sm font-medium">{t("settings.routing.warmupModel.label")}</p>
                <p className="text-xs text-muted-foreground">
                  {t("settings.routing.warmupModel.description")}
                </p>
              </div>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                value={draft.warmupModel}
                disabled={busy}
                maxLength={WARMUP_MODEL_MAX_LENGTH}
                onChange={(event) => updateDraft({ warmupModel: event.target.value })}
                className="h-8 text-xs"
                aria-label={t("settings.routing.warmupModel.label")}
              />
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs sm:w-24"
                disabled={busy || !warmupModelChanged || !warmupModelValid}
                onClick={() => void save({ warmupModel: draft.warmupModel.trim() })}
              >
                {t("settings.routing.warmupModel.save")}
              </Button>
            </div>
          </div>

          <div className="flex items-center justify-between gap-4 p-3">
            <div>
              <p className="text-sm font-medium">{t("settings.routing.upstream.label")}</p>
              <p className="text-xs text-muted-foreground">
                {t("settings.routing.upstream.description")}
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
                <SelectItem value="default">{t("settings.routing.upstream.default")}</SelectItem>
                <SelectItem value="auto">{t("settings.routing.upstream.auto")}</SelectItem>
                <SelectItem value="http">{t("settings.routing.upstream.http")}</SelectItem>
                <SelectItem value="websocket">{t("settings.routing.upstream.websocket")}</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center justify-between gap-4 p-3">
            <div>
              <p className="text-sm font-medium">{t("settings.routing.httpDownstream.label")}</p>
              <p className="text-xs text-muted-foreground">
                {t("settings.routing.httpDownstream.description")}
              </p>
            </div>
            <Select
              value={settings.httpDownstreamTransportPolicy}
              onValueChange={(value) =>
                save({
                  httpDownstreamTransportPolicy:
                    value as DashboardSettings["httpDownstreamTransportPolicy"],
                })
              }
            >
              <SelectTrigger className="h-8 w-52 text-xs" disabled={busy} aria-label={t("settings.routing.httpDownstream.label")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                {(["smart", "always_http", "always_websocket", "pinned"] as const).map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`settings.routing.httpDownstream.policies.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center justify-between gap-4 p-3">
            <div>
              <p className="text-sm font-medium">{t("settings.routing.strategy.label")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.routing.strategy.description")}</p>
            </div>
            <Select
              value={settings.routingStrategy}
              onValueChange={(value) => {
                const routingStrategy = value as DashboardSettings["routingStrategy"];
                if (routingStrategy === "single_account") {
                  const selectedAccountId = settings.singleAccountId ?? firstAccountId;
                  if (!selectedAccountId) {
                    return;
                  }
                  save({
                    routingStrategy,
                    singleAccountId: selectedAccountId,
                  });
                  return;
                }
                save({
                  routingStrategy,
                });
              }}
            >
              <SelectTrigger className="h-8 w-48 text-xs" disabled={busy}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                <SelectItem value="capacity_weighted">{t("settings.routing.strategy.capacityWeighted")}</SelectItem>
                <SelectItem value="relative_availability">{t("settings.routing.strategy.relativeAvailability")}</SelectItem>
                <SelectItem value="fill_first">{t("settings.routing.strategy.fillFirst")}</SelectItem>
                <SelectItem value="sequential_drain">{t("settings.routing.strategy.sequentialDrain")}</SelectItem>
                <SelectItem value="reset_drain">{t("settings.routing.strategy.resetDrain")}</SelectItem>
                <SelectItem value="single_account" disabled={!settings.singleAccountId && !firstAccountId}>
                  {t("settings.routing.strategy.singleAccount")}
                </SelectItem>
                <SelectItem value="usage_weighted">{t("settings.routing.strategy.usageWeighted")}</SelectItem>
                <SelectItem value="round_robin">{t("settings.routing.strategy.roundRobin")}</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-3 p-3">
            <div>
              <p className="text-sm font-medium">{t("settings.routing.additionalQuota.title")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.routing.additionalQuota.description")}</p>
            </div>
            <div className="space-y-2">
              {additionalQuotaRows.map(({ quotaKey, label, policy, hasOverride }) => (
                <div key={quotaKey} className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <div className="min-w-0 flex-1 truncate rounded-md border bg-muted/20 px-2 py-1.5 text-xs">
                    {label}
                  </div>
                  <Select
                    value={policy}
                    onValueChange={(value) =>
                      saveAdditionalQuotaPolicy(quotaKey, value as AdditionalQuotaRoutingPolicy)
                    }
                  >
                    <SelectTrigger
                      className="h-8 w-full text-xs sm:w-36"
                      disabled={busy}
                      aria-label={t("settings.routing.additionalQuota.policyAria", { quotaKey })}
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent align="end">
                      <SelectItem value="inherit">{t("settings.routing.additionalQuota.policies.inherit")}</SelectItem>
                      <SelectItem value="normal">{t("settings.routing.additionalQuota.policies.normal")}</SelectItem>
                      <SelectItem value="burn_first">{t("settings.routing.additionalQuota.policies.burnFirst")}</SelectItem>
                      <SelectItem value="preserve">{t("settings.routing.additionalQuota.policies.preserve")}</SelectItem>
                    </SelectContent>
                  </Select>
                  {hasOverride ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-8 text-xs sm:w-20"
                      disabled={busy}
                      onClick={() => removeAdditionalQuotaPolicy(quotaKey)}
                    >
                      {t("settings.routing.additionalQuota.reset")}
                    </Button>
                  ) : null}
                </div>
              ))}
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <Input
                  value={draft.additionalQuotaKey}
                  disabled={busy}
                  onChange={(event) => updateDraft({ additionalQuotaKey: event.target.value })}
                  className="h-8 text-xs"
                  aria-label={t("settings.routing.additionalQuota.keyAria")}
                  placeholder={t("settings.routing.additionalQuota.keyPlaceholder")}
                />
                <Select
                  value={draft.additionalQuotaPolicy}
                  onValueChange={(value) => updateDraft({ additionalQuotaPolicy: value as AdditionalQuotaRoutingPolicy })}
                >
                  <SelectTrigger
                    className="h-8 w-full text-xs sm:w-36"
                    disabled={busy}
                    aria-label={t("settings.routing.additionalQuota.selectAria")}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent align="end">
                    <SelectItem value="inherit">{t("settings.routing.additionalQuota.policies.inherit")}</SelectItem>
                    <SelectItem value="normal">{t("settings.routing.additionalQuota.policies.normal")}</SelectItem>
                    <SelectItem value="burn_first">{t("settings.routing.additionalQuota.policies.burnFirst")}</SelectItem>
                    <SelectItem value="preserve">{t("settings.routing.additionalQuota.policies.preserve")}</SelectItem>
                  </SelectContent>
                </Select>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-8 text-xs sm:w-24"
                  disabled={busy || !draft.additionalQuotaKey.trim()}
                  onClick={() => saveAdditionalQuotaPolicy(draft.additionalQuotaKey, draft.additionalQuotaPolicy)}
                >
                  {t("settings.routing.additionalQuota.save")}
                </Button>
              </div>
            </div>
          </div>

          {relativeAvailabilitySelected ? (
            <>
              <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm font-medium">{t("settings.routing.relativeAvailability.powerLabel")}</p>
                  <p className="text-xs text-muted-foreground">
                    {t("settings.routing.relativeAvailability.powerDescription")}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Input
                    aria-label={t("settings.routing.relativeAvailability.powerLabel")}
                    type="number"
                    min={0.1}
                    step={0.1}
                    inputMode="decimal"
                    value={draft.relativeAvailabilityPower}
                    disabled={busy}
                    onChange={(event) => updateDraft({ relativeAvailabilityPower: event.target.value })}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && relativeAvailabilityPowerChanged) {
                        void save({ relativeAvailabilityPower: parsedRelativeAvailabilityPower });
                      }
                    }}
                    className="h-8 w-28 text-xs"
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-8 text-xs"
                    disabled={busy || !relativeAvailabilityPowerChanged}
                    onClick={() => void save({ relativeAvailabilityPower: parsedRelativeAvailabilityPower })}
                  >
                    {t("settings.routing.relativeAvailability.savePower")}
                  </Button>
                </div>
              </div>

              <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm font-medium">{t("settings.routing.relativeAvailability.topKLabel")}</p>
                  <p className="text-xs text-muted-foreground">
                    {t("settings.routing.relativeAvailability.topKDescription")}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Input
                    aria-label={t("settings.routing.relativeAvailability.topKLabel")}
                    type="number"
                    min={1}
                    max={20}
                    step={1}
                    inputMode="numeric"
                    value={draft.relativeAvailabilityTopK}
                    disabled={busy}
                    onChange={(event) => updateDraft({ relativeAvailabilityTopK: event.target.value })}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && relativeAvailabilityTopKChanged) {
                        void save({ relativeAvailabilityTopK: parsedRelativeAvailabilityTopK });
                      }
                    }}
                    className="h-8 w-28 text-xs"
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-8 text-xs"
                    disabled={busy || !relativeAvailabilityTopKChanged}
                    onClick={() => void save({ relativeAvailabilityTopK: parsedRelativeAvailabilityTopK })}
                  >
                    {t("settings.routing.relativeAvailability.saveTopK")}
                  </Button>
                </div>
              </div>
            </>
          ) : null}

          {settings.routingStrategy === "single_account" ? (
            <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-medium">{t("settings.routing.singleAccount.label")}</p>
                <p className="text-xs text-muted-foreground">
                  {t("settings.routing.singleAccount.description")}
                </p>
              </div>
              <Select
                value={settings.singleAccountId ?? undefined}
                onValueChange={(value) => save({ singleAccountId: value })}
              >
                <SelectTrigger
                  aria-label={t("settings.routing.singleAccount.label")}
                  className="h-8 w-full text-xs sm:w-64"
                  disabled={busy || accountsLoading || selectableAccounts.length === 0}
                >
                  <SelectValue
                    placeholder={
                      accountsLoading
                        ? t("settings.routing.singleAccount.loading")
                        : t("settings.routing.singleAccount.placeholder")
                    }
                  />
                </SelectTrigger>
                <SelectContent align="end">
                  {blockedSelectedAccount ? (
                    <SelectItem key={blockedSelectedAccount.accountId} value={blockedSelectedAccount.accountId} disabled>
                      {accountLabel(blockedSelectedAccount)}
                    </SelectItem>
                  ) : null}
                  {selectableAccounts.map((account) => (
                    <SelectItem key={account.accountId} value={account.accountId}>
                      {accountLabel(account)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!accountsLoading && selectableAccounts.length === 0 ? (
                <p className="text-xs text-muted-foreground">{t("settings.routing.singleAccount.empty")}</p>
              ) : null}
            </div>
          ) : null}

          <div className="flex items-center justify-between p-3">
            <div>
              <p className="text-sm font-medium">{t("settings.routing.stickyThreads.label")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.routing.stickyThreads.description")}</p>
            </div>
            <Switch
              aria-label={t("settings.routing.stickyThreads.ariaLabel")}
              checked={settings.stickyThreadsEnabled}
              disabled={busy}
              onCheckedChange={(checked) => save({ stickyThreadsEnabled: checked })}
            />
          </div>

          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">{t("settings.routing.stickyThresholds.primaryLabel")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.routing.stickyThresholds.primaryDescription")}</p>
            </div>
            <div className="flex items-center gap-2">
              <Input
                aria-label={t("settings.routing.stickyThresholds.primaryLabel")}
                type="number"
                min={0}
                max={100}
                step={0.1}
                inputMode="decimal"
                value={draft.stickyPrimaryThreshold}
                disabled={busy}
                onChange={(event) => updateDraft({ stickyPrimaryThreshold: event.target.value })}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && stickyPrimaryThresholdChanged) {
                    void save({
                      stickyReallocationPrimaryBudgetThresholdPct: parsedStickyPrimaryThreshold,
                    });
                  }
                }}
                className="h-8 w-28 text-xs"
              />
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs"
                disabled={busy || !stickyPrimaryThresholdChanged}
                onClick={() =>
                  void save({
                    stickyReallocationPrimaryBudgetThresholdPct: parsedStickyPrimaryThreshold,
                  })
                }
              >
                {t("settings.routing.stickyThresholds.savePrimary")}
              </Button>
            </div>
          </div>

          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">{t("settings.routing.stickyThresholds.secondaryLabel")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.routing.stickyThresholds.secondaryDescription")}</p>
            </div>
            <div className="flex items-center gap-2">
              <Input
                aria-label={t("settings.routing.stickyThresholds.secondaryLabel")}
                type="number"
                min={0}
                max={100}
                step={0.1}
                inputMode="decimal"
                value={draft.stickySecondaryThreshold}
                disabled={busy}
                onChange={(event) => updateDraft({ stickySecondaryThreshold: event.target.value })}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && stickySecondaryThresholdChanged) {
                    void save({
                      stickyReallocationSecondaryBudgetThresholdPct: parsedStickySecondaryThreshold,
                    });
                  }
                }}
                className="h-8 w-28 text-xs"
              />
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs"
                disabled={busy || !stickySecondaryThresholdChanged}
                onClick={() =>
                  void save({
                    stickyReallocationSecondaryBudgetThresholdPct: parsedStickySecondaryThreshold,
                  })
                }
              >
                {t("settings.routing.stickyThresholds.saveSecondary")}
              </Button>
            </div>
          </div>

          <div className="flex items-center justify-between p-3">
            <div>
              <p className="text-sm font-medium">{t("settings.routing.preferEarlier.label")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.routing.preferEarlier.description")}</p>
            </div>
            <div className="flex items-center gap-3">
              <Select
                value={settings.preferEarlierResetWindow}
                onValueChange={(value) => save({ preferEarlierResetWindow: value as "primary" | "secondary" })}
              >
                <SelectTrigger
                  aria-label={t("settings.routing.preferEarlier.windowAria")}
                  className="h-8 w-36 text-xs"
                  disabled={busy || !settings.preferEarlierResetAccounts}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent align="end">
                  <SelectItem value="secondary">{t("settings.routing.quotaWindows.weekly")}</SelectItem>
                  <SelectItem value="primary">{t("settings.routing.quotaWindows.fiveHour")}</SelectItem>
                </SelectContent>
              </Select>
              <Switch
                aria-label={t("settings.routing.preferEarlier.ariaLabel")}
                checked={settings.preferEarlierResetAccounts}
                disabled={busy}
                onCheckedChange={(checked) => save({ preferEarlierResetAccounts: checked })}
              />
            </div>
          </div>

          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">{t("settings.routing.workingDays.label")}</p>
              <p className="text-xs text-muted-foreground">{t("settings.routing.workingDays.description")}</p>
            </div>
            <div className="grid grid-cols-7 gap-1">
              {WEEKDAYS.map((day) => (
                <label
                  key={day.value}
                  className="flex min-w-0 flex-col items-center gap-1 rounded-md border bg-background px-2 py-1.5 text-[11px] font-medium"
                >
                  <Checkbox
                    aria-label={t("settings.routing.workingDays.dayAria", {
                      day: t(`settings.routing.workingDays.days.${day.key}`),
                    })}
                    checked={workingDays.has(day.value)}
                    disabled={busy || (workingDays.size === 1 && workingDays.has(day.value))}
                    onCheckedChange={(checked) => toggleWorkingDay(day.value, checked === true)}
                  />
                  {t(`settings.routing.workingDays.days.${day.key}`)}
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-3 p-3">
            <div className="flex items-center justify-between gap-4">
              <div className="flex min-w-0 items-center gap-2.5">
                <Zap className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
                <div>
                  <p className="text-sm font-medium">{t("settings.routing.limitWarmup.label")}</p>
                  <p className="text-xs text-muted-foreground">{t("settings.routing.limitWarmup.description")}</p>
                </div>
              </div>
              <Switch
                aria-label={t("settings.routing.limitWarmup.ariaLabel")}
                checked={settings.limitWarmupEnabled}
                disabled={busy}
                onCheckedChange={(checked) => save({ limitWarmupEnabled: checked })}
              />
            </div>
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium">Stagger idle warm-up</p>
                <p className="text-xs text-muted-foreground">
                  Spread opted-in account warm-ups across the rolling 5h window.
                </p>
              </div>
              <Switch
                aria-label="Enable staggered idle warm-up"
                checked={settings.limitWarmupStaggeredIdleEnabled}
                disabled={busy || !settings.limitWarmupEnabled}
                onCheckedChange={(checked) => save({ limitWarmupStaggeredIdleEnabled: checked })}
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
                  <SelectItem value="both">{t("settings.routing.limitWarmup.windows.both")}</SelectItem>
                  <SelectItem value="primary">{t("settings.routing.limitWarmup.windows.primary")}</SelectItem>
                  <SelectItem value="secondary">{t("settings.routing.limitWarmup.windows.secondary")}</SelectItem>
                </SelectContent>
              </Select>
              <Input
                value={draft.limitWarmupModel}
                disabled={busy}
                maxLength={LIMIT_WARMUP_MODEL_MAX_LENGTH}
                onChange={(event) => updateDraft({ limitWarmupModel: event.target.value })}
                className="h-8 text-xs"
                aria-label={t("settings.routing.limitWarmup.modelAria")}
              />
              <Input
                type="number"
                min={60}
                step={60}
                inputMode="numeric"
                value={draft.limitWarmupCooldown}
                disabled={busy}
                onChange={(event) => updateDraft({ limitWarmupCooldown: event.target.value })}
                className="h-8 text-xs"
                aria-label={t("settings.routing.limitWarmup.cooldownAria")}
              />
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                value={draft.limitWarmupPrompt}
                disabled={busy}
                maxLength={LIMIT_WARMUP_PROMPT_MAX_LENGTH}
                onChange={(event) => updateDraft({ limitWarmupPrompt: event.target.value })}
                className="h-8 text-xs"
                aria-label={t("settings.routing.limitWarmup.promptAria")}
              />
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs sm:w-24"
                disabled={busy || !limitWarmupFieldsChanged || !limitWarmupFieldsValid}
                onClick={() =>
                  void save({
                    limitWarmupModel: draft.limitWarmupModel.trim(),
                    limitWarmupPrompt: draft.limitWarmupPrompt.trim(),
                    limitWarmupCooldownSeconds: parsedLimitWarmupCooldown,
                  })
                }
              >
                {t("settings.routing.limitWarmup.save")}
              </Button>
            </div>
          </div>

          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">{t("settings.routing.promptCacheTtl.label")}</p>
              <p className="text-xs text-muted-foreground">
                {t("settings.routing.promptCacheTtl.description")}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Input
                aria-label={t("settings.routing.promptCacheTtl.label")}
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                value={draft.cacheAffinityTtl}
                disabled={busy}
                onChange={(event) => updateDraft({ cacheAffinityTtl: event.target.value })}
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
                {t("settings.routing.promptCacheTtl.save")}
              </Button>
            </div>
          </div>

        </div>
      </div>
    </section>
  );
}
