import { z } from "zod";

import { AccountAdditionalQuotaSchema, AccountSummarySchema } from "@/features/accounts/schemas";
import type { AccountSummary } from "@/features/accounts/schemas";

export { AccountAdditionalQuotaSchema, AccountSummarySchema };
export type { AccountSummary };
export type { AccountAdditionalQuota as AdditionalQuota } from "@/features/accounts/schemas";

const OverviewTimeframeKeySchema = z.enum(["1d", "7d", "30d"]);
export type OverviewTimeframe = z.infer<typeof OverviewTimeframeKeySchema>;
export const DEFAULT_OVERVIEW_TIMEFRAME: OverviewTimeframe = "7d";

export function parseOverviewTimeframe(value: string | null | undefined): OverviewTimeframe {
  const parsed = OverviewTimeframeKeySchema.safeParse(value);
  return parsed.success ? parsed.data : DEFAULT_OVERVIEW_TIMEFRAME;
}

const UsageHistoryItemSchema = z.object({
  accountId: z.string(),
  remainingPercentAvg: z.number().nullable(),
  capacityCredits: z.number(),
  remainingCredits: z.number(),
});

export const UsageWindowSchema = z.object({
  windowKey: z.string(),
  windowMinutes: z.number().nullable(),
  accounts: z.array(UsageHistoryItemSchema),
});

const UsageSummaryWindowSchema = z.object({
  remainingPercent: z.number(),
  capacityCredits: z.number(),
  remainingCredits: z.number(),
  resetAt: z.iso.datetime({ offset: true }).nullable(),
  windowMinutes: z.number().nullable(),
});

const DashboardOverviewTimeframeSchema = z.object({
  key: OverviewTimeframeKeySchema,
  windowMinutes: z.number().int().positive(),
  bucketSeconds: z.number().int().positive(),
  bucketCount: z.number().int().positive(),
});

const UsageCostSchema = z.object({
  currency: z.string(),
  totalUsd: z.number(),
});

const DashboardMetricsSchema = z.object({
  requests: z.number().nullable(),
  tokens: z.number().nullable(),
  cachedInputTokens: z.number().nullable(),
  errorRate: z.number().nullable(),
  errorCount: z.number().nullable(),
  topError: z.string().nullable(),
});

const DashboardMetricsComparisonPreviousSchema = z.object({
  requests: z.number(),
  tokens: z.number(),
  costUsd: z.number(),
});

const DashboardMetricsComparisonSchema = z.object({
  canCompare: z.boolean(),
  previous: DashboardMetricsComparisonPreviousSchema,
});

const TrendPointSchema = z.object({
  t: z.iso.datetime({ offset: true }),
  v: z.number(),
});

const MetricsTrendsSchema = z.object({
  requests: z.array(TrendPointSchema),
  tokens: z.array(TrendPointSchema),
  cost: z.array(TrendPointSchema),
  errorRate: z.array(TrendPointSchema),
});

export const DepletionSchema = z.object({
  risk: z.number(),
  riskLevel: z.enum(["safe", "warning", "danger", "critical"]),
  burnRate: z.number(),
  safeUsagePercent: z.number(),
  projectedExhaustionAt: z.iso.datetime({ offset: true }).nullable().optional(),
  secondsUntilExhaustion: z.number().nullable().optional(),
});

const WeeklyCreditPaceSchema = z.object({
  totalFullCredits: z.number(),
  totalActualRemainingCredits: z.number(),
  totalExpectedRemainingCredits: z.number(),
  actualUsedPercent: z.number(),
  scheduledUsedPercent: z.number(),
  deltaPercent: z.number(),
  scheduleGapCredits: z.number(),
  overPlanCredits: z.number(),
  projectedShortfallCredits: z.number(),
  pauseForBreakEvenHours: z.number().nullable(),
  paceMultiplier: z.number().nullable(),
  throttleToPercent: z.number().nullable(),
  reduceByPercent: z.number().nullable(),
  proAccountEquivalentToCoverOverPlan: z.number().nullable(),
  proAccountsToCoverOverPlan: z.number().int().nullable(),
  projectedDepletionHours: z.number().nullable(),
  projectedMinimumRemainingCredits: z.number().nullable(),
  forecastBurnRateCreditsPerHour: z.number().nullable(),
  scheduledBurnRateCreditsPerHour: z.number(),
  status: z.enum(["behind", "on_track", "ahead", "danger"]),
  accountCount: z.number().int().nonnegative(),
  staleAccountCount: z.number().int().nonnegative(),
  inactiveAccountCount: z.number().int().nonnegative(),
  confidence: z.enum(["high", "medium", "low"]),
});

export const DashboardOverviewSchema = z.object({
  lastSyncAt: z.iso.datetime({ offset: true }).nullable(),
  timeframe: DashboardOverviewTimeframeSchema,
  accounts: z.array(AccountSummarySchema),
  summary: z.object({
    primaryWindow: UsageSummaryWindowSchema,
    secondaryWindow: UsageSummaryWindowSchema.nullable(),
    cost: UsageCostSchema,
    metrics: DashboardMetricsSchema.nullable(),
    comparison: DashboardMetricsComparisonSchema.optional(),
  }),
  windows: z.object({
    primary: UsageWindowSchema,
    secondary: UsageWindowSchema.nullable(),
  }),
  trends: MetricsTrendsSchema,
  additionalQuotas: z.array(AccountAdditionalQuotaSchema).default([]),
  depletionPrimary: DepletionSchema.nullable().optional(),
  depletionSecondary: DepletionSchema.nullable().optional(),
  weeklyCreditPace: WeeklyCreditPaceSchema.nullable().optional(),
});

export const DashboardProjectionsSchema = z.object({
  depletionPrimary: DepletionSchema.nullable().optional(),
  depletionSecondary: DepletionSchema.nullable().optional(),
  weeklyCreditPace: WeeklyCreditPaceSchema.nullable().optional(),
});

const RequestLogCostBreakdownSchema = z.object({
  inputUsd: z.number().nullable().optional().default(null),
  cachedInputUsd: z.number().nullable().optional().default(null),
  outputUsd: z.number().nullable().optional().default(null),
  totalUsd: z.number().nullable().optional().default(null),
});

export const RequestLogSchema = z.object({
  requestedAt: z.iso.datetime({ offset: true }),
  accountId: z.string().nullable(),
  planType: z.string().nullable().optional().default(null),
  apiKeyName: z.string().nullable().optional().default(null),
  apiKeyId: z.string().nullable().optional().default(null),
  requestId: z.string(),
  requestKind: z.enum(["normal", "warmup", "limit_warmup"]).optional().default("normal"),
  model: z.string(),
  source: z.string().nullable().optional().default(null),
  transport: z.string().nullable().optional().default(null),
  upstreamTransport: z.string().nullable().optional(),
  useragent: z.string().nullable().optional().default(null),
  useragentGroup: z.string().nullable().optional().default(null),
  serviceTier: z.string().nullable().optional().default(null),
  requestedServiceTier: z.string().nullable().optional().default(null),
  actualServiceTier: z.string().nullable().optional().default(null),
  status: z.string(),
  errorCode: z.string().nullable(),
  errorMessage: z.string().nullable(),
  failurePhase: z.string().nullable().optional().default(null),
  failureDetail: z.string().nullable().optional().default(null),
  failureExceptionType: z.string().nullable().optional().default(null),
  upstreamStatusCode: z.number().int().nullable().optional().default(null),
  upstreamErrorCode: z.string().nullable().optional().default(null),
  bridgeStage: z.string().nullable().optional().default(null),
  tokens: z.number().nullable(),
  inputTokens: z.number().nullable().optional().default(null),
  outputTokens: z.number().nullable().optional().default(null),
  cachedInputTokens: z.number().nullable(),
  reasoningEffort: z.string().nullable(),
  costUsd: z.number().nullable(),
  costBreakdown: RequestLogCostBreakdownSchema.nullable().optional().default(null),
  latencyMs: z.number().nullable(),
});

export const RequestLogsResponseSchema = z.object({
  requests: z.array(RequestLogSchema),
  total: z.number().int().nonnegative(),
  hasMore: z.boolean(),
});

const RequestLogModelOptionSchema = z.object({
  model: z.string(),
  reasoningEffort: z.string().nullable(),
});

const RequestLogApiKeyOptionSchema = z.object({
  id: z.string(),
  name: z.string(),
  keyPrefix: z.string().nullable().optional().default(null),
});

export const RequestLogFilterOptionsSchema = z.object({
  accountIds: z.array(z.string()),
  modelOptions: z.array(RequestLogModelOptionSchema),
  apiKeys: z.array(RequestLogApiKeyOptionSchema),
  statuses: z.array(z.string()),
});

export const FilterStateSchema = z.object({
  search: z.string(),
  timeframe: z.enum(["all", "1h", "24h", "7d"]),
  accountIds: z.array(z.string()),
  apiKeyIds: z.array(z.string()),
  modelOptions: z.array(z.string()),
  statuses: z.array(z.string()),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
});

export type DashboardMetrics = z.infer<typeof DashboardMetricsSchema>;
export type DashboardMetricsComparison = z.infer<typeof DashboardMetricsComparisonSchema>;
export type DashboardOverview = z.infer<typeof DashboardOverviewSchema>;
export type DashboardProjections = z.infer<typeof DashboardProjectionsSchema>;
export type DashboardOverviewTimeframe = z.infer<typeof DashboardOverviewTimeframeSchema>;
export type TrendPoint = z.infer<typeof TrendPointSchema>;
export type MetricsTrends = z.infer<typeof MetricsTrendsSchema>;
export type UsageWindow = z.infer<typeof UsageWindowSchema>;
export type RequestLog = z.infer<typeof RequestLogSchema>;
export type RequestLogsResponse = z.infer<typeof RequestLogsResponseSchema>;
export type RequestLogFilterOptions = z.infer<typeof RequestLogFilterOptionsSchema>;
export type FilterState = z.infer<typeof FilterStateSchema>;
export type Depletion = z.infer<typeof DepletionSchema>;
export type ServerWeeklyCreditPace = z.infer<typeof WeeklyCreditPaceSchema>;
