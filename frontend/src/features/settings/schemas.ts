import { z } from "zod";

export const RoutingStrategySchema = z.enum([
  "usage_weighted",
  "round_robin",
  "capacity_weighted",
  "sequential_drain",
  "reset_drain",
  "single_account",
  "relative_availability",
  "fill_first",
]);
export const UpstreamStreamTransportSchema = z.enum([
  "default",
  "auto",
  "http",
  "websocket",
]);
export const LimitWarmupWindowsSchema = z.enum([
  "primary",
  "secondary",
  "both",
]);
export const AdditionalQuotaRoutingPolicySchema = z.enum([
  "inherit",
  "normal",
  "burn_first",
  "preserve",
]);
export const AdditionalQuotaPolicySchema = z.object({
  quotaKey: z.string(),
  displayLabel: z.string(),
  routingPolicy: AdditionalQuotaRoutingPolicySchema,
  modelIds: z.array(z.string()).optional().default([]),
});
const LimitWarmupModelSchema = z.string().min(1).max(128);
const LimitWarmupPromptSchema = z.string().min(1).max(512);

export const DashboardSettingsSchema = z
  .object({
    stickyThreadsEnabled: z.boolean(),
    upstreamStreamTransport:
      UpstreamStreamTransportSchema.optional().default("default"),
    preferEarlierResetAccounts: z.boolean(),
    preferEarlierResetWindow: z.enum(["primary", "secondary"]).optional().default("secondary"),
    routingStrategy: RoutingStrategySchema.optional().default("usage_weighted"),
    relativeAvailabilityPower: z.number().positive().optional().default(2),
    relativeAvailabilityTopK: z
      .number()
      .int()
      .min(1)
      .max(20)
      .optional()
      .default(5),
    singleAccountId: z.string().nullable().optional().default(null),
    openaiCacheAffinityMaxAgeSeconds: z
      .number()
      .int()
      .positive()
      .optional()
      .default(300),
    dashboardSessionTtlSeconds: z
      .number()
      .int()
      .min(3600)
      .optional()
      .default(43200),
    stickyReallocationBudgetThresholdPct: z.number().min(0).max(100).optional(),
    stickyReallocationPrimaryBudgetThresholdPct: z.number().min(0).max(100).optional(),
    stickyReallocationSecondaryBudgetThresholdPct: z.number().min(0).max(100).optional(),
    additionalQuotaRoutingPolicies: z
      .record(z.string(), AdditionalQuotaRoutingPolicySchema)
      .optional(),
    additionalQuotaPolicies: z.array(AdditionalQuotaPolicySchema).optional().default([]),
    warmupModel: z.string().trim().min(1).optional().default("gpt-5.4-mini"),
    importWithoutOverwrite: z.boolean(),
    totpRequiredOnLogin: z.boolean(),
    totpConfigured: z.boolean(),
    apiKeyAuthEnabled: z.boolean(),
    limitWarmupEnabled: z.boolean().optional().default(false),
    limitWarmupWindows: LimitWarmupWindowsSchema.optional().default("both"),
    limitWarmupModel: LimitWarmupModelSchema.optional().default("auto"),
    limitWarmupPrompt: LimitWarmupPromptSchema.optional().default("Say OK."),
    limitWarmupCooldownSeconds: z.number().int().min(60).optional().default(3600),
    limitWarmupMinAvailablePercent: z
      .number()
      .positive()
      .max(100)
      .optional()
      .default(100),
  })
  .transform((settings) => {
    const legacyProvided = settings.stickyReallocationBudgetThresholdPct !== undefined;
    const primaryProvided = settings.stickyReallocationPrimaryBudgetThresholdPct !== undefined;
    const secondaryProvided = settings.stickyReallocationSecondaryBudgetThresholdPct !== undefined;
    const primaryThreshold =
      settings.stickyReallocationPrimaryBudgetThresholdPct ??
      settings.stickyReallocationBudgetThresholdPct ??
      95;
    return {
      ...settings,
      stickyReallocationBudgetThresholdPct:
        settings.stickyReallocationBudgetThresholdPct ?? primaryThreshold,
      stickyReallocationPrimaryBudgetThresholdPct: primaryThreshold,
      stickyReallocationSecondaryBudgetThresholdPct:
        settings.stickyReallocationSecondaryBudgetThresholdPct ??
        settings.stickyReallocationBudgetThresholdPct ??
        100,
      __stickyReallocationBudgetThresholdPctProvided: legacyProvided,
      __stickyReallocationPrimaryBudgetThresholdPctProvided: primaryProvided,
      __stickyReallocationSecondaryBudgetThresholdPctProvided: secondaryProvided,
    };
  });

export const SettingsUpdateRequestSchema = z.object({
  stickyThreadsEnabled: z.boolean().optional(),
  upstreamStreamTransport: UpstreamStreamTransportSchema.optional(),
  preferEarlierResetAccounts: z.boolean().optional(),
  preferEarlierResetWindow: z.enum(["primary", "secondary"]).optional(),
  routingStrategy: RoutingStrategySchema.optional(),
  relativeAvailabilityPower: z.number().positive().optional(),
  relativeAvailabilityTopK: z.number().int().min(1).max(20).optional(),
  singleAccountId: z.string().nullable().optional(),
  openaiCacheAffinityMaxAgeSeconds: z.number().int().positive().optional(),
  dashboardSessionTtlSeconds: z.number().int().min(3600).optional(),
  stickyReallocationBudgetThresholdPct: z.number().min(0).max(100).optional(),
  stickyReallocationPrimaryBudgetThresholdPct: z.number().min(0).max(100).optional(),
  stickyReallocationSecondaryBudgetThresholdPct: z.number().min(0).max(100).optional(),
  additionalQuotaRoutingPolicies: z
    .record(z.string(), AdditionalQuotaRoutingPolicySchema)
    .optional(),
  warmupModel: z.string().trim().min(1).optional(),
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

type ParsedDashboardSettings = z.infer<typeof DashboardSettingsSchema>;
type StickyThresholdPresenceFlags = Pick<
  ParsedDashboardSettings,
  | "__stickyReallocationBudgetThresholdPctProvided"
  | "__stickyReallocationPrimaryBudgetThresholdPctProvided"
  | "__stickyReallocationSecondaryBudgetThresholdPctProvided"
>;
type StickyThresholdValues = Pick<
  ParsedDashboardSettings,
  | "stickyReallocationBudgetThresholdPct"
  | "stickyReallocationPrimaryBudgetThresholdPct"
  | "stickyReallocationSecondaryBudgetThresholdPct"
>;

export type DashboardSettings = Omit<
  ParsedDashboardSettings,
  keyof StickyThresholdPresenceFlags | keyof StickyThresholdValues
> &
  Partial<StickyThresholdPresenceFlags> &
  Partial<StickyThresholdValues>;
export type SettingsUpdateRequest = z.infer<typeof SettingsUpdateRequestSchema>;
export type AdditionalQuotaRoutingPolicy = z.infer<typeof AdditionalQuotaRoutingPolicySchema>;
