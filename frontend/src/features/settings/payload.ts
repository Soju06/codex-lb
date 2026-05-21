import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export function buildSettingsUpdateRequest(
  settings: DashboardSettings,
  patch: Partial<SettingsUpdateRequest>,
): SettingsUpdateRequest {
  const payload: SettingsUpdateRequest = {
    stickyThreadsEnabled: settings.stickyThreadsEnabled,
    upstreamStreamTransport: settings.upstreamStreamTransport,
    preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
    routingStrategy: settings.routingStrategy,
    relativeAvailabilityPower: settings.relativeAvailabilityPower,
    relativeAvailabilityTopK: settings.relativeAvailabilityTopK,
    openaiCacheAffinityMaxAgeSeconds: settings.openaiCacheAffinityMaxAgeSeconds,
    dashboardSessionTtlSeconds: settings.dashboardSessionTtlSeconds,
    stickyReallocationBudgetThresholdPct: settings.stickyReallocationBudgetThresholdPct,
    stickyReallocationPrimaryBudgetThresholdPct: settings.stickyReallocationPrimaryBudgetThresholdPct,
    stickyReallocationSecondaryBudgetThresholdPct: settings.stickyReallocationSecondaryBudgetThresholdPct,
    importWithoutOverwrite: settings.importWithoutOverwrite,
    totpRequiredOnLogin: settings.totpRequiredOnLogin,
    apiKeyAuthEnabled: settings.apiKeyAuthEnabled,
    limitWarmupEnabled: settings.limitWarmupEnabled,
    limitWarmupWindows: settings.limitWarmupWindows,
    limitWarmupModel: settings.limitWarmupModel,
    limitWarmupPrompt: settings.limitWarmupPrompt,
    limitWarmupCooldownSeconds: settings.limitWarmupCooldownSeconds,
    limitWarmupMinAvailablePercent: settings.limitWarmupMinAvailablePercent,
    ...patch,
  };
  if (
    settings.__stickyReallocationBudgetThresholdPctProvided === false &&
    !("stickyReallocationBudgetThresholdPct" in patch)
  ) {
    delete payload.stickyReallocationBudgetThresholdPct;
  }
  if (
    settings.__stickyReallocationPrimaryBudgetThresholdPctProvided === false &&
    !("stickyReallocationPrimaryBudgetThresholdPct" in patch)
  ) {
    delete payload.stickyReallocationPrimaryBudgetThresholdPct;
  }
  if (
    settings.__stickyReallocationSecondaryBudgetThresholdPctProvided === false &&
    !("stickyReallocationSecondaryBudgetThresholdPct" in patch)
  ) {
    delete payload.stickyReallocationSecondaryBudgetThresholdPct;
  }
  return payload;
}
