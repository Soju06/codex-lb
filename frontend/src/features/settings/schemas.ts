import { z } from "zod";

export const RoutingStrategySchema = z.enum(["usage_weighted", "round_robin", "capacity_weighted"]);
export const UpstreamStreamTransportSchema = z.enum(["default", "auto", "http", "websocket"]);
export const LimitWarmupWindowsSchema = z.enum(["primary", "secondary", "both"]);
const LimitWarmupModelSchema = z.string().min(1).max(128);
const LimitWarmupPromptSchema = z.string().min(1).max(512);

export const DashboardSettingsSchema = z.object({
  stickyThreadsEnabled: z.boolean(),
  upstreamStreamTransport: UpstreamStreamTransportSchema.optional().default("default"),
  preferEarlierResetAccounts: z.boolean(),
  routingStrategy: RoutingStrategySchema.optional().default("usage_weighted"),
  openaiCacheAffinityMaxAgeSeconds: z.number().int().positive().optional().default(300),
  dashboardSessionTtlSeconds: z.number().int().min(3600).optional().default(43200),
  importWithoutOverwrite: z.boolean(),
  totpRequiredOnLogin: z.boolean(),
  totpConfigured: z.boolean(),
  apiKeyAuthEnabled: z.boolean(),
  limitWarmupEnabled: z.boolean().optional().default(false),
  limitWarmupWindows: LimitWarmupWindowsSchema.optional().default("both"),
  limitWarmupModel: LimitWarmupModelSchema.optional().default("auto"),
  limitWarmupPrompt: LimitWarmupPromptSchema.optional().default("Say OK."),
  limitWarmupCooldownSeconds: z.number().int().min(60).optional().default(3600),
  limitWarmupMinAvailablePercent: z.number().positive().max(100).optional().default(100),
});

export const SettingsUpdateRequestSchema = z.object({
  stickyThreadsEnabled: z.boolean(),
  upstreamStreamTransport: UpstreamStreamTransportSchema.optional(),
  preferEarlierResetAccounts: z.boolean(),
  routingStrategy: RoutingStrategySchema.optional(),
  openaiCacheAffinityMaxAgeSeconds: z.number().int().positive().optional(),
  dashboardSessionTtlSeconds: z.number().int().min(3600).optional(),
  importWithoutOverwrite: z.boolean().optional(),
  totpRequiredOnLogin: z.boolean().optional(),
  apiKeyAuthEnabled: z.boolean().optional(),
  limitWarmupEnabled: z.boolean().optional(),
  limitWarmupWindows: LimitWarmupWindowsSchema.optional(),
  limitWarmupModel: LimitWarmupModelSchema.optional(),
  limitWarmupPrompt: LimitWarmupPromptSchema.optional(),
  limitWarmupCooldownSeconds: z.number().int().min(60).optional(),
  limitWarmupMinAvailablePercent: z.number().positive().max(100).optional(),
});

export type DashboardSettings = z.infer<typeof DashboardSettingsSchema>;
export type SettingsUpdateRequest = z.infer<typeof SettingsUpdateRequestSchema>;
