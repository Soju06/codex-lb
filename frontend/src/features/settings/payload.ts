import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export function buildSettingsUpdateRequest(
  settings: DashboardSettings,
  patch: Partial<SettingsUpdateRequest>,
): SettingsUpdateRequest {
  return {
    stickyThreadsEnabled: settings.stickyThreadsEnabled,
    preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
    routingStrategy: settings.routingStrategy,
    openaiCacheAffinityMaxAgeSeconds: settings.openaiCacheAffinityMaxAgeSeconds,
    importWithoutOverwrite: settings.importWithoutOverwrite,
    totpRequiredOnLogin: settings.totpRequiredOnLogin,
    apiKeyAuthEnabled: settings.apiKeyAuthEnabled,
    ...patch,
  };
}
