import type {
  DashboardSettings,
  SettingsUpdateRequest,
} from "@/features/settings/schemas";

export function buildSettingsUpdateRequest(
  settings: DashboardSettings,
  patch: Partial<SettingsUpdateRequest>,
): SettingsUpdateRequest {
  const payload: SettingsUpdateRequest = {
    expectedVersion: settings.version,
    stickyThreadsEnabled: settings.stickyThreadsEnabled,
    upstreamStreamTransport: settings.upstreamStreamTransport,
    prohibitFastMode: settings.prohibitFastMode,
    httpDownstreamTransportPolicy: settings.httpDownstreamTransportPolicy,
    preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
    preferEarlierResetWindow: settings.preferEarlierResetWindow,
    showResetCreditBadges: settings.showResetCreditBadges,
    autoRedeemResetCreditsBeforeExpiry: settings.autoRedeemResetCreditsBeforeExpiry,
    showResetCreditExpiryBadge: settings.showResetCreditExpiryBadge,
    routingStrategy: settings.routingStrategy,
    relativeAvailabilityPower: settings.relativeAvailabilityPower,
    relativeAvailabilityTopK: settings.relativeAvailabilityTopK,
    singleAccountId: settings.singleAccountId,
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
    limitWarmupExhaustedThresholdPercent: settings.limitWarmupExhaustedThresholdPercent,
    limitWarmupIdleThresholdPercent: settings.limitWarmupIdleThresholdPercent,
    limitWarmupMinAvailablePercent: settings.limitWarmupMinAvailablePercent,
    limitWarmupStaggeredIdleEnabled: settings.limitWarmupStaggeredIdleEnabled,
    weeklyPaceWorkingDays: settings.weeklyPaceWorkingDays,
    weeklyPaceSmoothingMinutes: settings.weeklyPaceSmoothingMinutes,
    guestAccessEnabled: settings.guestAccessEnabled,
    hideUpstreamQuotaFromApiKeys: settings.hideUpstreamQuotaFromApiKeys,
    ...patch,
  };
  if (payload.expectedVersion === undefined) {
    delete payload.expectedVersion;
  }
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
  return payload;
}
