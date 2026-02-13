import { get, put } from "@/lib/api-client";
import {
  DashboardSettingsSchema,
  SettingsUpdateRequestSchema,
} from "@/features/settings/schemas";

const SETTINGS_PATH = "/api/settings";

export function getSettings() {
  return get(SETTINGS_PATH, DashboardSettingsSchema);
}

export function updateSettings(payload: unknown) {
  const validated = SettingsUpdateRequestSchema.parse(payload);
  return put(SETTINGS_PATH, DashboardSettingsSchema, {
    body: validated,
  });
}
