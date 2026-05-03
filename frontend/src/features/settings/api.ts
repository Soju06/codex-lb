import { del, get, put } from "@/lib/api-client";
import {
  DashboardSettingsSchema,
  SettingsUpdateRequestSchema,
  UpstreamProxyGroupSchema,
  UpstreamProxyGroupUpsertRequestSchema,
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

export function listUpstreamProxyGroups() {
  return get(`${SETTINGS_PATH}/upstream-proxy-groups`, UpstreamProxyGroupSchema.array());
}

export function upsertUpstreamProxyGroup(name: string, payload: unknown) {
  const validated = UpstreamProxyGroupUpsertRequestSchema.parse(payload);
  return put(`${SETTINGS_PATH}/upstream-proxy-groups/${encodeURIComponent(name)}`, UpstreamProxyGroupSchema, {
    body: validated,
  });
}

export function deleteUpstreamProxyGroup(name: string) {
  return del(`${SETTINGS_PATH}/upstream-proxy-groups/${encodeURIComponent(name)}`);
}
