import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export function buildSettingsUpdateRequest(
  settings: DashboardSettings,
  patch: Partial<SettingsUpdateRequest>,
): SettingsUpdateRequest {
  return {
    stickyThreadsEnabled: settings.stickyThreadsEnabled,
    upstreamStreamTransport: settings.upstreamStreamTransport,
    preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
    routingStrategy: settings.routingStrategy,
    relativeAvailabilityPower: settings.relativeAvailabilityPower,
    relativeAvailabilityTopK: settings.relativeAvailabilityTopK,
    openaiCacheAffinityMaxAgeSeconds: settings.openaiCacheAffinityMaxAgeSeconds,
    dashboardSessionTtlSeconds: settings.dashboardSessionTtlSeconds,
    importWithoutOverwrite: settings.importWithoutOverwrite,
    totpRequiredOnLogin: settings.totpRequiredOnLogin,
    apiKeyAuthEnabled: settings.apiKeyAuthEnabled,
    ...patch,
  };
}
