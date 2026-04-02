import { z } from "zod";

export const LIMIT_TYPES = ["total_tokens", "input_tokens", "output_tokens", "cost_usd", "credits"] as const;
export const LIMIT_WINDOWS = ["daily", "weekly", "monthly", "5h", "7d"] as const;

export type LimitType = (typeof LIMIT_TYPES)[number];
export type LimitWindowType = (typeof LIMIT_WINDOWS)[number];

export const LimitRuleSchema = z.object({
  id: z.number(),
  limitType: z.enum(LIMIT_TYPES),
  limitWindow: z.enum(LIMIT_WINDOWS),
  maxValue: z.number(),
  currentValue: z.number(),
  modelFilter: z.string().nullable(),
  resetAt: z.string().datetime({ offset: true }),
});

export const LimitRuleCreateSchema = z.object({
  limitType: z.enum(LIMIT_TYPES),
  limitWindow: z.enum(LIMIT_WINDOWS),
  maxValue: z.number().int().positive(),
  modelFilter: z.string().nullable().optional(),
});

export const ApiKeyUsageSummarySchema = z.object({
  requestCount: z.number().int().nonnegative(),
  totalTokens: z.number().int().nonnegative(),
  cachedInputTokens: z.number().int().nonnegative(),
  totalCostUsd: z.number().nonnegative().default(0),
});

export const SERVICE_TIERS = ["auto", "default", "priority", "flex"] as const;
export type ServiceTierType = (typeof SERVICE_TIERS)[number];

export const ApiKeySchema = z.object({
  id: z.string(),
  name: z.string(),
  keyPrefix: z.string(),
  allowedModels: z.array(z.string()).nullable(),
  enforcedModel: z.string().nullable().default(null),
  enforcedReasoningEffort: z
    .enum(["none", "minimal", "low", "medium", "high", "xhigh"])
    .nullable()
    .default(null),
  enforcedServiceTier: z
    .enum(SERVICE_TIERS)
    .nullable()
    .default(null),
  expiresAt: z.string().datetime({ offset: true }).nullable(),
  isActive: z.boolean(),
  createdAt: z.string().datetime({ offset: true }),
  lastUsedAt: z.string().datetime({ offset: true }).nullable(),
  limits: z.array(LimitRuleSchema).default([]),
  usageSummary: ApiKeyUsageSummarySchema.nullable().default(null),
});

export const ApiKeyCreateRequestSchema = z.object({
  name: z.string().min(1).max(128),
  allowedModels: z.array(z.string()).optional(),
  enforcedModel: z.string().min(1).nullable().optional(),
  enforcedReasoningEffort: z
    .enum(["none", "minimal", "low", "medium", "high", "xhigh"])
    .nullable()
    .optional(),
  enforcedServiceTier: z
    .enum(SERVICE_TIERS)
    .nullable()
    .optional(),
  weeklyTokenLimit: z.number().int().positive().nullable().optional(),
  expiresAt: z.string().datetime({ offset: true }).nullable().optional(),
  limits: z.array(LimitRuleCreateSchema).optional(),
});

export const ApiKeyCreateResponseSchema = ApiKeySchema.extend({
  key: z.string(),
});

export const ApiKeyUpdateRequestSchema = z.object({
  name: z.string().min(1).max(128).optional(),
  allowedModels: z.array(z.string()).nullable().optional(),
  enforcedModel: z.string().min(1).nullable().optional(),
  enforcedReasoningEffort: z
    .enum(["none", "minimal", "low", "medium", "high", "xhigh"])
    .nullable()
    .optional(),
  enforcedServiceTier: z
    .enum(SERVICE_TIERS)
    .nullable()
    .optional(),
  weeklyTokenLimit: z.number().int().positive().nullable().optional(),
  expiresAt: z.string().datetime({ offset: true }).nullable().optional(),
  isActive: z.boolean().optional(),
  limits: z.array(LimitRuleCreateSchema).optional(),
  resetUsage: z.boolean().optional(),
});

export const ApiKeyListSchema = z.array(ApiKeySchema);

export type LimitRule = z.infer<typeof LimitRuleSchema>;
export type LimitRuleCreate = z.infer<typeof LimitRuleCreateSchema>;
export type ApiKey = z.infer<typeof ApiKeySchema>;
export type ApiKeyCreateRequest = z.infer<typeof ApiKeyCreateRequestSchema>;
export type ApiKeyCreateResponse = z.infer<typeof ApiKeyCreateResponseSchema>;
export type ApiKeyUpdateRequest = z.infer<typeof ApiKeyUpdateRequestSchema>;

export const ModelItemSchema = z.object({ id: z.string(), name: z.string() });
export const ModelsResponseSchema = z.object({ models: z.array(ModelItemSchema) });
export type ModelItem = z.infer<typeof ModelItemSchema>;
