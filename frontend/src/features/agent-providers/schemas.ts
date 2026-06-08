import { z } from "zod";

export const AgentProviderCapabilitySchema = z.object({
  protocol: z.enum(["codex_chatgpt", "gemini_api", "vertex_ai", "antigravity_cli", "interactions_api"]),
  status: z.enum(["ready", "foundation", "planned"]),
  proxyable: z.boolean(),
  streaming: z.boolean(),
  lifecycleNotes: z.string(),
  operatorAction: z.string(),
  availableUntil: z.string().nullable(),
  notes: z.string(),
});

export const AgentProviderSummarySchema = z.object({
  providerId: z.enum(["codex", "gemini", "antigravity"]),
  displayName: z.string(),
  status: z.enum(["ready", "foundation", "planned"]),
  authModes: z.array(z.enum(["chatgpt_oauth", "api_key", "google_cloud_adc", "cli_keyring"])),
  quotaDimensions: z.array(z.string()),
  dashboardSections: z.array(z.string()),
  capabilities: z.array(AgentProviderCapabilitySchema),
});

export const AgentProviderListSchema = z.object({
  providers: z.array(AgentProviderSummarySchema),
});

export const ProviderOverviewTimeframeSchema = z.enum(["1d", "7d", "30d"]);

export const AgentProviderOverviewItemSchema = z.object({
  providerId: z.enum(["codex", "gemini", "antigravity"]),
  displayName: z.string(),
  status: z.enum(["ready", "foundation", "planned"]),
  accountCount: z.number().int().nonnegative(),
  activeAccountCount: z.number().int().nonnegative(),
  quotaWindowCount: z.number().int().nonnegative(),
  requestCount: z.number().int().nonnegative(),
  successCount: z.number().int().nonnegative(),
  errorCount: z.number().int().nonnegative(),
  inputTokens: z.number().int().nonnegative(),
  outputTokens: z.number().int().nonnegative(),
  cachedInputTokens: z.number().int().nonnegative(),
});

export const AgentProviderOverviewTotalsSchema = z.object({
  providerCount: z.number().int().nonnegative(),
  accountCount: z.number().int().nonnegative(),
  activeAccountCount: z.number().int().nonnegative(),
  quotaWindowCount: z.number().int().nonnegative(),
  requestCount: z.number().int().nonnegative(),
  successCount: z.number().int().nonnegative(),
  errorCount: z.number().int().nonnegative(),
  inputTokens: z.number().int().nonnegative(),
  outputTokens: z.number().int().nonnegative(),
  cachedInputTokens: z.number().int().nonnegative(),
});

export const AgentProviderOverviewSchema = z.object({
  timeframe: ProviderOverviewTimeframeSchema,
  providers: z.array(AgentProviderOverviewItemSchema),
  totals: AgentProviderOverviewTotalsSchema,
});

export type AgentProviderCapability = z.infer<typeof AgentProviderCapabilitySchema>;
export type AgentProviderSummary = z.infer<typeof AgentProviderSummarySchema>;
export type AgentProviderList = z.infer<typeof AgentProviderListSchema>;
export type ProviderOverviewTimeframe = z.infer<typeof ProviderOverviewTimeframeSchema>;
export type AgentProviderOverviewItem = z.infer<typeof AgentProviderOverviewItemSchema>;
export type AgentProviderOverview = z.infer<typeof AgentProviderOverviewSchema>;
