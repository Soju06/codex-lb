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

export type RoutingSettingsProps = {
  settings: DashboardSettings;
  accounts?: AccountSummary[];
  accountsLoading?: boolean;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

function accountLabel(account: AccountSummary): string {
  const name = account.alias?.trim() || account.displayName?.trim() || account.email?.trim() || account.accountId;
  const compactId = formatCompactAccountId(account.accountId, 6, 4);
  return `${name} (${compactId})`;
}

export function RoutingSettings({
  settings,
  accounts = [],
  accountsLoading = false,
  busy,
  onSave,
}: RoutingSettingsProps) {
  const [warmupModel, setWarmupModel] = useState(settings.warmupModel);
  const [cacheAffinityTtl, setCacheAffinityTtl] = useState(
    String(settings.openaiCacheAffinityMaxAgeSeconds),
  );
  const [relativeAvailabilityPower, setRelativeAvailabilityPower] = useState(
    String(settings.relativeAvailabilityPower),
  );
  const [relativeAvailabilityTopK, setRelativeAvailabilityTopK] = useState(
    String(settings.relativeAvailabilityTopK),
  );
  const [stickyPrimaryThreshold, setStickyPrimaryThreshold] = useState(
    String(settings.stickyReallocationPrimaryBudgetThresholdPct ?? 95),
  );
  const [stickySecondaryThreshold, setStickySecondaryThreshold] = useState(
    String(settings.stickyReallocationSecondaryBudgetThresholdPct ?? 100),
  );
  const [limitWarmupModel, setLimitWarmupModel] = useState(settings.limitWarmupModel);
  const [limitWarmupPrompt, setLimitWarmupPrompt] = useState(settings.limitWarmupPrompt);
  const [limitWarmupCooldown, setLimitWarmupCooldown] = useState(String(settings.limitWarmupCooldownSeconds));
  const [additionalQuotaKey, setAdditionalQuotaKey] = useState("");
  const [additionalQuotaPolicy, setAdditionalQuotaPolicy] =
    useState<AdditionalQuotaRoutingPolicy>("inherit");

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

  const parsedCacheAffinityTtl = Number.parseInt(cacheAffinityTtl, 10);
  const cacheAffinityTtlValid = Number.isInteger(parsedCacheAffinityTtl) && parsedCacheAffinityTtl > 0;
  const cacheAffinityTtlChanged =
    cacheAffinityTtlValid && parsedCacheAffinityTtl !== settings.openaiCacheAffinityMaxAgeSeconds;
  const warmupModelChanged = warmupModel.trim() !== settings.warmupModel;
  const warmupModelValid = warmupModel.trim().length > 0 && warmupModel.trim().length <= WARMUP_MODEL_MAX_LENGTH;
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

  const parsedRelativeAvailabilityPower = Number.parseFloat(relativeAvailabilityPower);
  const relativeAvailabilityPowerValid =
    Number.isFinite(parsedRelativeAvailabilityPower) && parsedRelativeAvailabilityPower > 0;
  const relativeAvailabilityPowerChanged =
    relativeAvailabilityPowerValid && parsedRelativeAvailabilityPower !== settings.relativeAvailabilityPower;

  const relativeAvailabilityTopKTrimmed = relativeAvailabilityTopK.trim();
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
    ...Object.entries(additionalQuotaOverrides)
      .filter(([quotaKey]) => !knownAdditionalQuotaKeys.has(quotaKey))
      .map(([quotaKey, policy]) => ({
        quotaKey,
        label: quotaKey,
        policy,
        hasOverride: true,
      })),
  ];
  const parsedStickyPrimaryThreshold = Number.parseFloat(stickyPrimaryThreshold);
  const stickyPrimaryThresholdValid =
    Number.isFinite(parsedStickyPrimaryThreshold) &&
    parsedStickyPrimaryThreshold >= 0 &&
    parsedStickyPrimaryThreshold <= 100;
  const stickyPrimaryThresholdChanged =
    stickyPrimaryThresholdValid &&
    parsedStickyPrimaryThreshold !== (settings.stickyReallocationPrimaryBudgetThresholdPct ?? 95);
  const parsedStickySecondaryThreshold = Number.parseFloat(stickySecondaryThreshold);
  const stickySecondaryThresholdValid =
    Number.isFinite(parsedStickySecondaryThreshold) &&
    parsedStickySecondaryThreshold >= 0 &&
    parsedStickySecondaryThreshold <= 100;
  const stickySecondaryThresholdChanged =
    stickySecondaryThresholdValid &&
    parsedStickySecondaryThreshold !== (settings.stickyReallocationSecondaryBudgetThresholdPct ?? 100);

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
          <div className="space-y-3 p-3">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-sm font-medium">Warmup model</p>
                <p className="text-xs text-muted-foreground">
                  Set the model used by the normal warmup endpoint.
                </p>
              </div>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                value={warmupModel}
                disabled={busy}
                maxLength={WARMUP_MODEL_MAX_LENGTH}
                onChange={(event) => setWarmupModel(event.target.value)}
                className="h-8 text-xs"
                aria-label="Warmup model"
              />
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs sm:w-24"
                disabled={busy || !warmupModelChanged || !warmupModelValid}
                onClick={() => void save({ warmupModel: warmupModel.trim() })}
              >
                Save warmup model
              </Button>
            </div>
          </div>

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
                <SelectItem value="capacity_weighted">Capacity weighted</SelectItem>
                <SelectItem value="relative_availability">Relative availability</SelectItem>
                <SelectItem value="fill_first">Fill first</SelectItem>
                <SelectItem value="sequential_drain">Sequential drain</SelectItem>
                <SelectItem value="reset_drain">Reset drain</SelectItem>
                <SelectItem value="single_account" disabled={!settings.singleAccountId && !firstAccountId}>
                  Single account
                </SelectItem>
                <SelectItem value="usage_weighted">Usage weighted</SelectItem>
                <SelectItem value="round_robin">Round robin</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-3 p-3">
            <div>
              <p className="text-sm font-medium">Additional quota routing policies</p>
              <p className="text-xs text-muted-foreground">Override account routing for model-specific quota pools.</p>
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
                      aria-label={`${quotaKey} routing policy`}
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent align="end">
                      <SelectItem value="inherit">Inherit</SelectItem>
                      <SelectItem value="normal">Normal</SelectItem>
                      <SelectItem value="burn_first">Burn first</SelectItem>
                      <SelectItem value="preserve">Preserve</SelectItem>
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
                      Reset
                    </Button>
                  ) : null}
                </div>
              ))}
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <Input
                  value={additionalQuotaKey}
                  disabled={busy}
                  onChange={(event) => setAdditionalQuotaKey(event.target.value)}
                  className="h-8 text-xs"
                  aria-label="Additional quota key"
                  placeholder="Quota key"
                />
                <Select
                  value={additionalQuotaPolicy}
                  onValueChange={(value) => setAdditionalQuotaPolicy(value as AdditionalQuotaRoutingPolicy)}
                >
                  <SelectTrigger
                    className="h-8 w-full text-xs sm:w-36"
                    disabled={busy}
                    aria-label="Additional quota routing policy"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent align="end">
                    <SelectItem value="inherit">Inherit</SelectItem>
                    <SelectItem value="normal">Normal</SelectItem>
                    <SelectItem value="burn_first">Burn first</SelectItem>
                    <SelectItem value="preserve">Preserve</SelectItem>
                  </SelectContent>
                </Select>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-8 text-xs sm:w-24"
                  disabled={busy || !additionalQuotaKey.trim()}
                  onClick={() => saveAdditionalQuotaPolicy(additionalQuotaKey, additionalQuotaPolicy)}
                >
                  Save policy
                </Button>
              </div>
            </div>
          </div>

          {relativeAvailabilitySelected ? (
            <>
              <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm font-medium">Relative availability power</p>
                  <p className="text-xs text-muted-foreground">
                    Raise normalized relative-availability scores to this power before weighted selection.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Input
                    aria-label="Relative availability power"
                    type="number"
                    min={0.1}
                    step={0.1}
                    inputMode="decimal"
                    value={relativeAvailabilityPower}
                    disabled={busy}
                    onChange={(event) => setRelativeAvailabilityPower(event.target.value)}
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
                    Save power
                  </Button>
                </div>
              </div>

              <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm font-medium">Relative availability top K</p>
                  <p className="text-xs text-muted-foreground">
                    Keep only the strongest weighted candidates before the final random draw.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Input
                    aria-label="Relative availability top K"
                    type="number"
                    min={1}
                    max={20}
                    step={1}
                    inputMode="numeric"
                    value={relativeAvailabilityTopK}
                    disabled={busy}
                    onChange={(event) => setRelativeAvailabilityTopK(event.target.value)}
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
                    Save top K
                  </Button>
                </div>
              </div>
            </>
          ) : null}

          {settings.routingStrategy === "single_account" ? (
            <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-medium">Selected account</p>
                <p className="text-xs text-muted-foreground">
                  Route every eligible request through one account until this setting changes.
                </p>
              </div>
              <Select
                value={settings.singleAccountId ?? undefined}
                onValueChange={(value) => save({ singleAccountId: value })}
              >
                <SelectTrigger
                  aria-label="Selected account"
                  className="h-8 w-full text-xs sm:w-64"
                  disabled={busy || accountsLoading || selectableAccounts.length === 0}
                >
                  <SelectValue placeholder={accountsLoading ? "Loading accounts..." : "Choose account"} />
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
                <p className="text-xs text-muted-foreground">Import an account before enabling single-account routing.</p>
              ) : null}
            </div>
          ) : null}

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

          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">Sticky primary threshold</p>
              <p className="text-xs text-muted-foreground">Reallocate sticky sessions above this primary usage percent.</p>
            </div>
            <div className="flex items-center gap-2">
              <Input
                aria-label="Sticky primary threshold"
                type="number"
                min={0}
                max={100}
                step={0.1}
                inputMode="decimal"
                value={stickyPrimaryThreshold}
                disabled={busy}
                onChange={(event) => setStickyPrimaryThreshold(event.target.value)}
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
                Save primary
              </Button>
            </div>
          </div>

          <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">Sticky secondary threshold</p>
              <p className="text-xs text-muted-foreground">Reallocate sticky sessions above this secondary usage percent.</p>
            </div>
            <div className="flex items-center gap-2">
              <Input
                aria-label="Sticky secondary threshold"
                type="number"
                min={0}
                max={100}
                step={0.1}
                inputMode="decimal"
                value={stickySecondaryThreshold}
                disabled={busy}
                onChange={(event) => setStickySecondaryThreshold(event.target.value)}
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
                Save secondary
              </Button>
            </div>
          </div>

          <div className="flex items-center justify-between p-3">
            <div>
              <p className="text-sm font-medium">Prefer earlier reset</p>
              <p className="text-xs text-muted-foreground">Bias traffic to accounts with earlier quota reset.</p>
            </div>
            <div className="flex items-center gap-3">
              <Select
                value={settings.preferEarlierResetWindow}
                onValueChange={(value) => save({ preferEarlierResetWindow: value as "primary" | "secondary" })}
              >
                <SelectTrigger
                  aria-label="Reset preference window"
                  className="h-8 w-36 text-xs"
                  disabled={busy || !settings.preferEarlierResetAccounts}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent align="end">
                  <SelectItem value="secondary">Weekly quota</SelectItem>
                  <SelectItem value="primary">5h quota</SelectItem>
                </SelectContent>
              </Select>
              <Switch
                aria-label="Prefer earlier reset accounts"
                checked={settings.preferEarlierResetAccounts}
                disabled={busy}
                onCheckedChange={(checked) => save({ preferEarlierResetAccounts: checked })}
              />
            </div>
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
                aria-label="Prompt-cache affinity TTL"
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                value={cacheAffinityTtl}
                disabled={busy}
                onChange={(event) => setCacheAffinityTtl(event.target.value)}
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
