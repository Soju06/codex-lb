import { z } from "zod";

export const RoutingStrategySchema = z.enum(["usage_weighted", "round_robin"]);
export const ForceReasoningEffortSchema = z.enum(["low", "normal", "medium", "high", "xhigh"]);

export const DashboardSettingsSchema = z.object({
  stickyThreadsEnabled: z.boolean(),
  preferEarlierResetAccounts: z.boolean(),
  routingStrategy: RoutingStrategySchema,
  globalModelForceEnabled: z.boolean(),
  globalModelForceModel: z.string().nullable(),
  globalModelForceReasoningEffort: ForceReasoningEffortSchema.nullable(),
  importWithoutOverwrite: z.boolean(),
  totpRequiredOnLogin: z.boolean(),
  totpConfigured: z.boolean(),
  apiKeyAuthEnabled: z.boolean(),
});

export const SettingsUpdateRequestSchema = z.object({
  stickyThreadsEnabled: z.boolean(),
  preferEarlierResetAccounts: z.boolean(),
  routingStrategy: RoutingStrategySchema.optional(),
  globalModelForceEnabled: z.boolean().optional(),
  globalModelForceModel: z.string().nullable().optional(),
  globalModelForceReasoningEffort: ForceReasoningEffortSchema.nullable().optional(),
  importWithoutOverwrite: z.boolean().optional(),
  totpRequiredOnLogin: z.boolean().optional(),
  apiKeyAuthEnabled: z.boolean().optional(),
});

export type DashboardSettings = z.infer<typeof DashboardSettingsSchema>;
export type SettingsUpdateRequest = z.infer<typeof SettingsUpdateRequestSchema>;
export type ForceReasoningEffort = z.infer<typeof ForceReasoningEffortSchema>;
