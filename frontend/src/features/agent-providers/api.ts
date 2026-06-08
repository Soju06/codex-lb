import { get, patch, post, put } from "@/lib/api-client";

import {
  AgentProviderAccountSchema,
  AgentProviderAccountUpdateSchema,
  AgentProviderAccountsSchema,
  AntigravityProviderAccountCreateSchema,
  type AgentProviderAccountUpdate,
  type AntigravityProviderAccountCreate,
  type GeminiProviderAccountCreate,
} from "@/features/agent-providers/accounts-schemas";
import {
  AgentProviderPreflightSchema,
  AgentProviderQuotaWindowSchema,
  AgentProviderQuotaWindowUpsertSchema,
  AgentProviderRoutingSettingsSchema,
  AgentProviderRoutingSettingsUpdateSchema,
  type AgentProviderQuotaWindowUpsert,
  type AgentProviderRoutingSettingsUpdate,
} from "@/features/agent-providers/routing-schemas";
import {
  AntigravityHarnessPrintRequestSchema,
  AntigravityHarnessPrintResponseSchema,
  AntigravityManagedInteractionRunRequestSchema,
  AntigravityManagedInteractionRunResponseSchema,
  type AntigravityHarnessPrintRequest,
  type AntigravityManagedInteractionRunRequest,
} from "@/features/agent-providers/harness-schemas";
import {
  AgentProviderListSchema,
  AgentProviderOverviewSchema,
  type ProviderOverviewTimeframe,
} from "@/features/agent-providers/schemas";

const AGENT_PROVIDERS_PATH = "/api/agent-providers";
export type AgentProviderId = "codex" | "gemini" | "antigravity";

export function getAgentProviders() {
  return get(AGENT_PROVIDERS_PATH, AgentProviderListSchema);
}

export function getAgentProviderOverview(timeframe: ProviderOverviewTimeframe = "7d") {
  return get(
    `${AGENT_PROVIDERS_PATH}/overview?timeframe=${encodeURIComponent(timeframe)}`,
    AgentProviderOverviewSchema,
  );
}

export function getAgentProviderAccounts(providerId: AgentProviderId) {
  return get(`${AGENT_PROVIDERS_PATH}/${providerId}/accounts`, AgentProviderAccountsSchema);
}

export async function createGeminiProviderAccount(payload: GeminiProviderAccountCreate) {
  return post(`${AGENT_PROVIDERS_PATH}/gemini/accounts`, AgentProviderAccountSchema, {
    body: payload,
  });
}

export function createAntigravityProviderAccount(payload: AntigravityProviderAccountCreate) {
  return post(`${AGENT_PROVIDERS_PATH}/antigravity/accounts`, AgentProviderAccountSchema, {
    body: AntigravityProviderAccountCreateSchema.parse(payload),
  });
}

export function updateAgentProviderAccount(
  providerId: AgentProviderId,
  accountId: string,
  payload: AgentProviderAccountUpdate,
) {
  const validated = AgentProviderAccountUpdateSchema.parse(payload);
  return patch(
    `${AGENT_PROVIDERS_PATH}/${providerId}/accounts/${encodeURIComponent(accountId)}`,
    AgentProviderAccountSchema,
    { body: validated },
  );
}

export function getAgentProviderRoutingSettings(providerId: AgentProviderId) {
  return get(`${AGENT_PROVIDERS_PATH}/${providerId}/routing/settings`, AgentProviderRoutingSettingsSchema);
}

export function updateAgentProviderRoutingSettings(
  providerId: AgentProviderId,
  payload: AgentProviderRoutingSettingsUpdate,
) {
  const validated = AgentProviderRoutingSettingsUpdateSchema.parse(payload);
  return patch(`${AGENT_PROVIDERS_PATH}/${providerId}/routing/settings`, AgentProviderRoutingSettingsSchema, {
    body: validated,
  });
}

export function upsertAgentProviderQuotaWindow(
  providerId: AgentProviderId,
  accountId: string,
  dimension: string,
  payload: AgentProviderQuotaWindowUpsert,
) {
  const validated = AgentProviderQuotaWindowUpsertSchema.parse(payload);
  return put(
    `${AGENT_PROVIDERS_PATH}/${providerId}/accounts/${encodeURIComponent(accountId)}/quota-windows/${encodeURIComponent(dimension)}`,
    AgentProviderQuotaWindowSchema,
    { body: validated },
  );
}

export function preflightAgentProviderRouting(providerId: AgentProviderId) {
  return post(`${AGENT_PROVIDERS_PATH}/${providerId}/routing/preflight`, AgentProviderPreflightSchema);
}

export function runAntigravityHarnessPrint(payload: AntigravityHarnessPrintRequest) {
  return post(`${AGENT_PROVIDERS_PATH}/antigravity/harness/print`, AntigravityHarnessPrintResponseSchema, {
    body: AntigravityHarnessPrintRequestSchema.parse(payload),
  });
}

export function runAntigravityManagedInteraction(payload: AntigravityManagedInteractionRunRequest) {
  return post(
    `${AGENT_PROVIDERS_PATH}/antigravity/interactions/run`,
    AntigravityManagedInteractionRunResponseSchema,
    {
      body: AntigravityManagedInteractionRunRequestSchema.parse(payload),
    },
  );
}
