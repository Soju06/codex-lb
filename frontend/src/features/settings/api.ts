import { del, get, post, put } from "@/lib/api-client";
import {
  AccountProxyBindingRequestSchema,
  AccountProxyBindingSchema,
  DashboardSettingsSchema,
  ModelContextWindowOverrideUpsertRequestSchema,
  ModelContextWindowOverridesSchema,
  SettingsUpdateRequestSchema,
  SubscriptionOverflowPreflightSchema,
  TelemetryConsentSchema,
  TelemetryConsentUpdateRequestSchema,
  UpstreamProxyAdminSchema,
  UpstreamProxyEndpointCreateRequestSchema,
  UpstreamProxyEndpointSchema,
  UpstreamProxyEndpointTestResponseSchema,
  UpstreamProxyPoolCreateRequestSchema,
  UpstreamProxyPoolMemberRequestSchema,
  UpstreamProxyPoolSchema,
} from "@/features/settings/schemas";

const SETTINGS_PATH = "/api/settings";
const UPSTREAM_PROXY_PATH = `${SETTINGS_PATH}/upstream-proxy`;
const TELEMETRY_PATH = `${SETTINGS_PATH}/telemetry`;
const MODEL_CONTEXT_WINDOW_OVERRIDES_PATH = `${SETTINGS_PATH}/model-context-window-overrides`;

export function getSettings() {
  return get(SETTINGS_PATH, DashboardSettingsSchema);
}

export function updateSettings(payload: unknown) {
  const validated = SettingsUpdateRequestSchema.parse(payload);
  return put(SETTINGS_PATH, DashboardSettingsSchema, {
    body: validated,
  });
}

export function getSubscriptionOverflowPreflight(sourceId: string) {
  return get(
    `${SETTINGS_PATH}/subscription-overflow/preflight?source_id=${encodeURIComponent(sourceId)}`,
    SubscriptionOverflowPreflightSchema,
  );
}

export function getTelemetryConsent(options: { includePreview?: boolean } = {}) {
  const path = options.includePreview ? `${TELEMETRY_PATH}?include_preview=true` : TELEMETRY_PATH;
  return get(path, TelemetryConsentSchema);
}

export function updateTelemetryConsent(payload: unknown) {
  const validated = TelemetryConsentUpdateRequestSchema.parse(payload);
  return put(TELEMETRY_PATH, TelemetryConsentSchema, {
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

export function testUpstreamProxyEndpoint(endpointId: string) {
  return post(
    `${UPSTREAM_PROXY_PATH}/endpoints/${encodeURIComponent(endpointId)}/test`,
    UpstreamProxyEndpointTestResponseSchema,
  );
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

// M4 model catalogue: per-model context window overrides (one dashboard row per slug).
export function getModelContextWindowOverrides() {
  return get(MODEL_CONTEXT_WINDOW_OVERRIDES_PATH, ModelContextWindowOverridesSchema);
}

export function putModelContextWindowOverride(slug: string, payload: unknown) {
  const validated = ModelContextWindowOverrideUpsertRequestSchema.parse(payload);
  return put(`${MODEL_CONTEXT_WINDOW_OVERRIDES_PATH}/${encodeURIComponent(slug)}`, ModelContextWindowOverridesSchema, {
    body: validated,
  });
}

export function deleteModelContextWindowOverride(slug: string) {
  return del(`${MODEL_CONTEXT_WINDOW_OVERRIDES_PATH}/${encodeURIComponent(slug)}`, ModelContextWindowOverridesSchema);
}
