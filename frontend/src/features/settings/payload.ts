import type {
  DashboardSettings,
  SettingsUpdateRequest,
} from "@/features/settings/schemas";

export function buildSettingsUpdateRequest(
  settings: DashboardSettings,
  patch: Partial<SettingsUpdateRequest>,
): SettingsUpdateRequest {
  const payload: SettingsUpdateRequest = {
    stickyThreadsEnabled: settings.stickyThreadsEnabled,
    upstreamStreamTransport: settings.upstreamStreamTransport,
    preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
    preferEarlierResetWindow: settings.preferEarlierResetWindow,
    routingStrategy: settings.routingStrategy,
    relativeAvailabilityPower: settings.relativeAvailabilityPower,
    relativeAvailabilityTopK: settings.relativeAvailabilityTopK,
    singleAccountId: settings.singleAccountId,
    manualAccountPriorityIds: settings.manualAccountPriorityIds,
    openaiCacheAffinityMaxAgeSeconds: settings.openaiCacheAffinityMaxAgeSeconds,
    dashboardSessionTtlSeconds: settings.dashboardSessionTtlSeconds,
    warmupModel: settings.warmupModel,
    stickyReallocationBudgetThresholdPct: settings.stickyReallocationBudgetThresholdPct,
    stickyReallocationPrimaryBudgetThresholdPct: settings.stickyReallocationPrimaryBudgetThresholdPct,
    stickyReallocationSecondaryBudgetThresholdPct: settings.stickyReallocationSecondaryBudgetThresholdPct,
    additionalQuotaRoutingPolicies: settings.additionalQuotaRoutingPolicies ?? {},
    importWithoutOverwrite: settings.importWithoutOverwrite,
    totpRequiredOnLogin: settings.totpRequiredOnLogin,
    apiKeyAuthEnabled: settings.apiKeyAuthEnabled,
    limitWarmupEnabled: settings.limitWarmupEnabled,
    limitWarmupWindows: settings.limitWarmupWindows,
    limitWarmupModel: settings.limitWarmupModel,
    limitWarmupPrompt: settings.limitWarmupPrompt,
    limitWarmupCooldownSeconds: settings.limitWarmupCooldownSeconds,
    limitWarmupMinAvailablePercent: settings.limitWarmupMinAvailablePercent,
    weeklyPaceWorkingDays: settings.weeklyPaceWorkingDays,
    ...patch,
  };
  if (
    (payload.stickyReallocationBudgetThresholdPct === undefined ||
      settings.__stickyReallocationBudgetThresholdPctProvided === false) &&
    !("stickyReallocationBudgetThresholdPct" in patch)
  ) {
    delete payload.stickyReallocationBudgetThresholdPct;
  }
  if (
    (payload.stickyReallocationPrimaryBudgetThresholdPct === undefined ||
      settings.__stickyReallocationPrimaryBudgetThresholdPctProvided === false) &&
    !("stickyReallocationPrimaryBudgetThresholdPct" in patch)
  ) {
    delete payload.stickyReallocationPrimaryBudgetThresholdPct;
  }
  if (
    (payload.stickyReallocationSecondaryBudgetThresholdPct === undefined ||
      settings.__stickyReallocationSecondaryBudgetThresholdPctProvided === false) &&
    !("stickyReallocationSecondaryBudgetThresholdPct" in patch)
  ) {
    delete payload.stickyReallocationSecondaryBudgetThresholdPct;
  }
  if (
    "stickyReallocationPrimaryBudgetThresholdPct" in patch &&
    !("stickyReallocationBudgetThresholdPct" in patch) &&
    settings.__stickyReallocationBudgetThresholdPctProvided !== false
  ) {
    payload.stickyReallocationBudgetThresholdPct = patch.stickyReallocationPrimaryBudgetThresholdPct;
  }
  if (
    !("manualAccountPriorityIds" in patch) &&
    settings.manualAccountPriorityIds.length === 0
  ) {
    delete payload.manualAccountPriorityIds;
  }
  return payload;
}
