import { z } from "zod";

export const AgentProviderRoutingStrategySchema = z.enum([
  "capacity_weighted",
  "round_robin",
  "sequential_drain",
  "reset_drain",
  "single_account",
  "ordered_fallback",
]);

export const AgentProviderQuotaWindowSchema = z.object({
  dimension: z.string(),
  used: z.number(),
  limit: z.number().nullable().optional(),
  resetAt: z.string().nullable().optional(),
  recordedAt: z.string(),
});

export const AgentProviderQuotaWindowUpsertSchema = z.object({
  dimension: z.string().min(1),
  used: z.number().min(0),
  limit: z.number().min(0).nullable().optional(),
  resetAt: z.string().nullable().optional(),
});

export const AgentProviderRoutingSettingsSchema = z.object({
  providerId: z.enum(["codex", "gemini", "antigravity"]),
  strategy: AgentProviderRoutingStrategySchema,
  singleAccountId: z.string().nullable().optional(),
  orderedAccountIds: z.array(z.string()).optional().default([]),
  quotaThresholdPct: z.number(),
  roundRobinCursor: z.string().nullable().optional(),
  createdAt: z.string(),
  updatedAt: z.string(),
});

export const AgentProviderRoutingSettingsUpdateSchema = z.object({
  strategy: AgentProviderRoutingStrategySchema.optional(),
  singleAccountId: z.string().nullable().optional(),
  orderedAccountIds: z.array(z.string()).optional(),
  quotaThresholdPct: z.number().min(0).max(100).optional(),
  roundRobinCursor: z.string().nullable().optional(),
});

export const AgentProviderPreflightAccountStateSchema = z.object({
  accountId: z.string(),
  displayName: z.string(),
  status: z.string(),
  quotaWindows: z.array(AgentProviderQuotaWindowSchema),
});

export const AgentProviderPreflightSchema = z.object({
  providerId: z.enum(["codex", "gemini", "antigravity"]),
  selectedAccountId: z.string().nullable().optional(),
  deniedReason: z.string().nullable().optional(),
  candidateAccountIds: z.array(z.string()),
  settings: AgentProviderRoutingSettingsSchema,
  accounts: z.array(AgentProviderPreflightAccountStateSchema),
});

export type AgentProviderRoutingStrategy = z.infer<typeof AgentProviderRoutingStrategySchema>;
export type AgentProviderQuotaWindow = z.infer<typeof AgentProviderQuotaWindowSchema>;
export type AgentProviderQuotaWindowUpsert = z.infer<typeof AgentProviderQuotaWindowUpsertSchema>;
export type AgentProviderRoutingSettings = z.infer<typeof AgentProviderRoutingSettingsSchema>;
export type AgentProviderRoutingSettingsUpdate = z.infer<typeof AgentProviderRoutingSettingsUpdateSchema>;
export type AgentProviderPreflight = z.infer<typeof AgentProviderPreflightSchema>;
