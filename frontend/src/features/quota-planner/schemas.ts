import { z } from "zod";

export const QuotaPlannerModeSchema = z.enum(["off", "shadow", "suggest", "auto"]);
export const QuotaPlannerForecastQuantileSchema = z.enum(["p50", "p75", "p90"]);

export const QuotaPlannerSettingsSchema = z.object({
  mode: QuotaPlannerModeSchema,
  timezone: z.string(),
  workingDays: z.array(z.number().int().min(0).max(6)),
  workingHoursStart: z.string(),
  workingHoursEnd: z.string(),
  prewarmEnabled: z.boolean(),
  prewarmLeadMinutes: z.number().int().min(0).max(1440),
  maxWarmupsPerDay: z.number().int().min(0),
  maxWarmupCreditsPerDay: z.number().min(0),
  minExpectedGain: z.number().min(0),
  forecastQuantile: QuotaPlannerForecastQuantileSchema,
  allowSyntheticTraffic: z.boolean(),
  warmupModelPreference: z.string().nullable(),
  dryRun: z.boolean(),
});

export const QuotaPlannerSettingsUpdateRequestSchema = QuotaPlannerSettingsSchema.partial();

export const QuotaPlannerDecisionSchema = z.object({
  id: z.string(),
  createdAt: z.string(),
  mode: QuotaPlannerModeSchema,
  accountId: z.string().nullable(),
  action: z.string(),
  scheduledAt: z.string().nullable(),
  executedAt: z.string().nullable(),
  score: z.number(),
  reason: z.string().nullable(),
  details: z.record(z.string(), z.unknown()).nullable().optional(),
  status: z.string(),
  idempotencyKey: z.string(),
});

export const QuotaPlannerForecastSlotSchema = z.object({
  slotStart: z.string(),
  demandUnits: z.number(),
  requestCount: z.number(),
  source: z.string(),
});

export const QuotaPlannerSimulationSchema = z.object({
  loss: z.number(),
  unmetDemand: z.number(),
  wastedCapacity: z.number(),
  coldStartPenalty: z.number(),
  synchronizationPenalty: z.number(),
  forecastUnits: z.number(),
  servedUnits: z.number(),
});

export const QuotaPlannerForecastSchema = z.object({
  generatedAt: z.string(),
  horizonHours: z.number().int(),
  slotSeconds: z.number().int(),
  totalDemandUnits: z.number(),
  peakSlotStart: z.string().nullable(),
  peakDemandUnits: z.number(),
  simulation: QuotaPlannerSimulationSchema,
  slots: z.array(QuotaPlannerForecastSlotSchema),
});

export const QuotaPlannerWarmNowRequestSchema = z.object({
  accountId: z.string().min(1),
  model: z.string().nullable().optional(),
  apiKeyId: z.string().nullable().optional(),
  forceProbe: z.boolean().optional(),
});

export const QuotaPlannerWarmupActionResponseSchema = z.object({
  decisionId: z.string(),
  status: z.string(),
  reason: z.string(),
  requestId: z.string().nullable(),
  executedAt: z.string().nullable(),
});

export type QuotaPlannerMode = z.infer<typeof QuotaPlannerModeSchema>;
export type QuotaPlannerForecastQuantile = z.infer<typeof QuotaPlannerForecastQuantileSchema>;
export type QuotaPlannerSettings = z.infer<typeof QuotaPlannerSettingsSchema>;
export type QuotaPlannerSettingsUpdateRequest = z.infer<typeof QuotaPlannerSettingsUpdateRequestSchema>;
export type QuotaPlannerDecision = z.infer<typeof QuotaPlannerDecisionSchema>;
export type QuotaPlannerForecast = z.infer<typeof QuotaPlannerForecastSchema>;
export type QuotaPlannerWarmNowRequest = z.infer<typeof QuotaPlannerWarmNowRequestSchema>;
