import { z } from "zod";

export const ApiKeyTrendPointSchema = z.object({
  t: z.string().datetime({ offset: true }),
  v: z.number(),
});

export const ApiKeyTrendsResponseSchema = z.object({
  keyId: z.string(),
  cost: z.array(ApiKeyTrendPointSchema),
  tokens: z.array(ApiKeyTrendPointSchema),
});

export const ApiKeyUsage7DayResponseSchema = z.object({
  keyId: z.string(),
  totalTokens: z.number().int(),
  totalCostUsd: z.number(),
  totalRequests: z.number().int(),
  cachedInputTokens: z.number().int(),
});

export const ApiKeyAccountUsage7DayItemSchema = z.object({
  accountId: z.string().nullable(),
  displayName: z.string(),
  totalCostUsd: z.number(),
  totalTokens: z.number().int(),
  totalRequests: z.number().int(),
});

export const ApiKeyAccountUsage7DayResponseSchema = z.object({
  keyId: z.string(),
  accounts: z.array(ApiKeyAccountUsage7DayItemSchema),
});

export type ApiKeyTrendPoint = z.infer<typeof ApiKeyTrendPointSchema>;
export type ApiKeyTrendsResponse = z.infer<typeof ApiKeyTrendsResponseSchema>;
export type ApiKeyUsage7DayResponse = z.infer<typeof ApiKeyUsage7DayResponseSchema>;
export type ApiKeyAccountUsage7DayItem = z.infer<typeof ApiKeyAccountUsage7DayItemSchema>;
export type ApiKeyAccountUsage7DayResponse = z.infer<typeof ApiKeyAccountUsage7DayResponseSchema>;
