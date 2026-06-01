import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

export function buildSettingsUpdateRequest(
  settings: DashboardSettings,
  patch: Partial<SettingsUpdateRequest>,
): SettingsUpdateRequest {
  return Object.fromEntries(Object.entries(patch).filter(([, value]) => value !== undefined)) as SettingsUpdateRequest;
}
