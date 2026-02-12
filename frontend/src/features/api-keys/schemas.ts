import { z } from "zod";

export const ApiKeySchema = z.object({
  id: z.string(),
  name: z.string(),
  keyPrefix: z.string(),
  allowedModels: z.array(z.string()).nullable(),
  weeklyTokenLimit: z.number().int().positive().nullable(),
  weeklyTokensUsed: z.number().int().nonnegative(),
  weeklyResetAt: z.string().datetime({ offset: true }),
  expiresAt: z.string().datetime({ offset: true }).nullable(),
  isActive: z.boolean(),
  createdAt: z.string().datetime({ offset: true }),
  lastUsedAt: z.string().datetime({ offset: true }).nullable(),
});

export const ApiKeyCreateRequestSchema = z.object({
  name: z.string().min(1).max(128),
  allowedModels: z.array(z.string()).optional(),
  weeklyTokenLimit: z.number().int().positive().nullable().optional(),
  expiresAt: z.string().datetime({ offset: true }).nullable().optional(),
});

export const ApiKeyCreateResponseSchema = ApiKeySchema.extend({
  key: z.string(),
});

export const ApiKeyUpdateRequestSchema = z.object({
  name: z.string().min(1).max(128).optional(),
  allowedModels: z.array(z.string()).nullable().optional(),
  weeklyTokenLimit: z.number().int().positive().nullable().optional(),
  expiresAt: z.string().datetime({ offset: true }).nullable().optional(),
  isActive: z.boolean().optional(),
});

export const ApiKeyListSchema = z.array(ApiKeySchema);

export type ApiKey = z.infer<typeof ApiKeySchema>;
export type ApiKeyCreateRequest = z.infer<typeof ApiKeyCreateRequestSchema>;
export type ApiKeyCreateResponse = z.infer<typeof ApiKeyCreateResponseSchema>;
export type ApiKeyUpdateRequest = z.infer<typeof ApiKeyUpdateRequestSchema>;
