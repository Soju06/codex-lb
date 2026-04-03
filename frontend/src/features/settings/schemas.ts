import { z } from "zod";

export const RoutingStrategySchema = z.enum(["usage_weighted", "round_robin", "capacity_weighted"]);
export const UpstreamStreamTransportSchema = z.enum(["default", "auto", "http", "websocket"]);
export const RequestVisibilityModeSchema = z.enum(["off", "persistent", "temporary"]);

export const DashboardSettingsSchema = z.object({
  stickyThreadsEnabled: z.boolean(),
  upstreamStreamTransport: UpstreamStreamTransportSchema,
  preferEarlierResetAccounts: z.boolean(),
  routingStrategy: RoutingStrategySchema,
  openaiCacheAffinityMaxAgeSeconds: z.number().int().positive(),
  importWithoutOverwrite: z.boolean(),
  totpRequiredOnLogin: z.boolean(),
  totpConfigured: z.boolean(),
  apiKeyAuthEnabled: z.boolean(),
  requestVisibilityMode: RequestVisibilityModeSchema,
  requestVisibilityExpiresAt: z.string().datetime({ offset: true }).nullable(),
  requestVisibilityEnabled: z.boolean(),
});

export const SettingsUpdateRequestSchema = z.object({
  stickyThreadsEnabled: z.boolean(),
  upstreamStreamTransport: UpstreamStreamTransportSchema.optional(),
  preferEarlierResetAccounts: z.boolean(),
  routingStrategy: RoutingStrategySchema.optional(),
  openaiCacheAffinityMaxAgeSeconds: z.number().int().positive().optional(),
  importWithoutOverwrite: z.boolean().optional(),
  totpRequiredOnLogin: z.boolean().optional(),
  apiKeyAuthEnabled: z.boolean().optional(),
  requestVisibilityMode: RequestVisibilityModeSchema.optional(),
  requestVisibilityDurationMinutes: z.number().int().positive().optional(),
});

export type DashboardSettings = z.infer<typeof DashboardSettingsSchema>;
export type RequestVisibilityMode = z.infer<typeof RequestVisibilityModeSchema>;
export type SettingsUpdateRequest = z.infer<typeof SettingsUpdateRequestSchema>;
