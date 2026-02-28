import { z } from "zod";

export const ModelOverrideMatchTypeSchema = z.enum(["ip", "app", "api_key"]);

export const ModelOverrideSchema = z.object({
  id: z.number().int(),
  matchType: ModelOverrideMatchTypeSchema,
  matchValue: z.string(),
  forcedModel: z.string(),
  forcedReasoningEffort: z.string().nullable(),
  enabled: z.boolean(),
  note: z.string().nullable(),
  createdAt: z.string().datetime({ offset: true }),
  updatedAt: z.string().datetime({ offset: true }),
});

export const ModelOverridesResponseSchema = z.object({
  items: z.array(ModelOverrideSchema),
});

export const ModelOverrideCreateRequestSchema = z.object({
  matchType: ModelOverrideMatchTypeSchema,
  matchValue: z.string().min(1),
  forcedModel: z.string().min(1),
  forcedReasoningEffort: z.string().nullable().optional(),
  enabled: z.boolean().default(true),
  note: z.string().nullable().optional(),
});

export const ModelOverrideUpdateRequestSchema = z.object({
  matchValue: z.string().min(1).optional(),
  forcedModel: z.string().min(1).optional(),
  forcedReasoningEffort: z.string().nullable().optional(),
  enabled: z.boolean().optional(),
  note: z.string().nullable().optional(),
});

export type ModelOverride = z.infer<typeof ModelOverrideSchema>;
export type ModelOverrideMatchType = z.infer<typeof ModelOverrideMatchTypeSchema>;
export type ModelOverrideCreateRequest = z.infer<typeof ModelOverrideCreateRequestSchema>;
export type ModelOverrideUpdateRequest = z.infer<typeof ModelOverrideUpdateRequestSchema>;

