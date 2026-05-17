import { z } from "zod";

export const RoutingStrategySchema = z.enum(["usage_weighted", "round_robin", "capacity_weighted"]);
export const UpstreamStreamTransportSchema = z.enum(["default", "auto", "http", "websocket"]);

export const DashboardSettingsSchema = z.object({
  stickyThreadsEnabled: z.boolean(),
  upstreamStreamTransport: UpstreamStreamTransportSchema,
  preferEarlierResetAccounts: z.boolean(),
  routingStrategy: RoutingStrategySchema,
  openaiCacheAffinityMaxAgeSeconds: z.number().int().positive(),
  dashboardSessionTtlSeconds: z.number().int().min(3600),
  importWithoutOverwrite: z.boolean(),
  totpRequiredOnLogin: z.boolean(),
  totpConfigured: z.boolean(),
  apiKeyAuthEnabled: z.boolean(),
  upstreamProxyConfigured: z.boolean().optional(),
  upstreamProxyUrl: z.string().nullable().optional(),
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
  upstreamProxyUrl: z.string().nullable().optional(),
});

export const UpstreamProxyGroupSchema = z.object({
  name: z.string(),
  proxyUrl: z.string(),
});

export const UpstreamProxyGroupUpsertRequestSchema = z.object({
  proxyUrl: z.string(),
});

export type DashboardSettings = z.infer<typeof DashboardSettingsSchema>;
export type SettingsUpdateRequest = z.infer<typeof SettingsUpdateRequestSchema>;
export type UpstreamProxyGroup = z.infer<typeof UpstreamProxyGroupSchema>;
