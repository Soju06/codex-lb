import { get, post, put } from "@/lib/api-client";
import {
  AccountProxyBindingRequestSchema,
  AccountProxyBindingSchema,
  DashboardSettingsSchema,
  SettingsUpdateRequestSchema,
  UpstreamProxyAdminSchema,
  UpstreamProxyEndpointCreateRequestSchema,
  UpstreamProxyEndpointSchema,
  UpstreamProxyPoolCreateRequestSchema,
  UpstreamProxyPoolMemberRequestSchema,
  UpstreamProxyPoolSchema,
} from "@/features/settings/schemas";

const SETTINGS_PATH = "/api/settings";
const UPSTREAM_PROXY_PATH = `${SETTINGS_PATH}/upstream-proxy`;

export function getSettings() {
  return get(SETTINGS_PATH, DashboardSettingsSchema);
}

export function updateSettings(payload: unknown) {
  const validated = SettingsUpdateRequestSchema.parse(payload);
  return put(SETTINGS_PATH, DashboardSettingsSchema, {
    body: validated,
  });
}

export function getUpstreamProxyAdmin() {
  return get(UPSTREAM_PROXY_PATH, UpstreamProxyAdminSchema);
}

export function createUpstreamProxyEndpoint(payload: unknown) {
  const validated = UpstreamProxyEndpointCreateRequestSchema.parse(payload);
  return post(`${UPSTREAM_PROXY_PATH}/endpoints`, UpstreamProxyEndpointSchema, {
    body: validated,
  });
}

export function createUpstreamProxyPool(payload: unknown) {
  const validated = UpstreamProxyPoolCreateRequestSchema.parse(payload);
  return post(`${UPSTREAM_PROXY_PATH}/pools`, UpstreamProxyPoolSchema, {
    body: validated,
  });
}

export function addUpstreamProxyPoolMember(poolId: string, payload: unknown) {
  const validated = UpstreamProxyPoolMemberRequestSchema.parse(payload);
  return post(`${UPSTREAM_PROXY_PATH}/pools/${encodeURIComponent(poolId)}/members`, UpstreamProxyPoolSchema, {
    body: validated,
  });
}

export function putAccountProxyBinding(accountId: string, payload: unknown) {
  const validated = AccountProxyBindingRequestSchema.parse(payload);
  return put(`${UPSTREAM_PROXY_PATH}/accounts/${encodeURIComponent(accountId)}/binding`, AccountProxyBindingSchema, {
    body: validated,
  });
}
