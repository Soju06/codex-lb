import type { SettingsUpdateRequest } from "@/features/settings/schemas";

export function buildSettingsUpdateRequest(patch: Partial<SettingsUpdateRequest>): SettingsUpdateRequest {
  return Object.fromEntries(Object.entries(patch).filter(([, value]) => value !== undefined)) as SettingsUpdateRequest;
}
