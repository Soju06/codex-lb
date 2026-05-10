import { z } from "zod";

import { AccountAdditionalQuotaSchema, AccountSummarySchema, AccountUsageSchema } from "@/features/accounts/schemas";
import type { AccountSummary } from "@/features/accounts/schemas";

export { AccountAdditionalQuotaSchema, AccountSummarySchema, AccountUsageSchema };
export type { AccountSummary };
export type { AccountAdditionalQuota as AdditionalQuota } from "@/features/accounts/schemas";

export const OverviewTimeframeKeySchema = z.enum(["1d", "7d", "30d"]);
export type OverviewTimeframe = z.infer<typeof OverviewTimeframeKeySchema>;
export const DEFAULT_OVERVIEW_TIMEFRAME: OverviewTimeframe = "7d";

export function parseOverviewTimeframe(value: string | null | undefined): OverviewTimeframe {
  const parsed = OverviewTimeframeKeySchema.safeParse(value);
  return parsed.success ? parsed.data : DEFAULT_OVERVIEW_TIMEFRAME;
}

export const UsageHistoryItemSchema = z.object({
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

export const UsageSummaryWindowSchema = z.object({
  remainingPercent: z.number(),
  capacityCredits: z.number(),
  remainingCredits: z.number(),
  resetAt: z.string().datetime({ offset: true }).nullable(),
  windowMinutes: z.number().nullable(),
});

export const DashboardOverviewTimeframeSchema = z.object({
  key: OverviewTimeframeKeySchema,
  windowMinutes: z.number().int().positive(),
  bucketSeconds: z.number().int().positive(),
  bucketCount: z.number().int().positive(),
});

export const UsageCostSchema = z.object({
  currency: z.string(),
  totalUsd: z.number(),
});

export const DashboardMetricsSchema = z.object({
  requests: z.number().nullable(),
  tokens: z.number().nullable(),
  cachedInputTokens: z.number().nullable(),
  errorRate: z.number().nullable(),
  errorCount: z.number().nullable(),
  topError: z.string().nullable(),
});

export const TrendPointSchema = z.object({
  t: z.string().datetime({ offset: true }),
  v: z.number(),
});

export const MetricsTrendsSchema = z.object({
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
  projectedExhaustionAt: z.string().datetime({ offset: true }).nullable().optional(),
  secondsUntilExhaustion: z.number().nullable().optional(),
});

export const DashboardOverviewSchema = z.object({
  lastSyncAt: z.string().datetime({ offset: true }).nullable(),
  timeframe: DashboardOverviewTimeframeSchema,
  accounts: z.array(AccountSummarySchema),
  summary: z.object({
    primaryWindow: UsageSummaryWindowSchema,
    secondaryWindow: UsageSummaryWindowSchema.nullable(),
    cost: UsageCostSchema,
    metrics: DashboardMetricsSchema.nullable(),
  }),
  windows: z.object({
    primary: UsageWindowSchema,
    secondary: UsageWindowSchema.nullable(),
  }),
  trends: MetricsTrendsSchema,
  additionalQuotas: z.array(AccountAdditionalQuotaSchema).default([]),
  depletionPrimary: DepletionSchema.nullable().optional(),
  depletionSecondary: DepletionSchema.nullable().optional(),
});

const RequestLogSlimSummarySchema = z.preprocess(
  (value) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return value;
    }
    const summary = value as Record<string, unknown>;
    return {
      historicalToolOutputsSlimmed:
        summary.historicalToolOutputsSlimmed ?? summary.historical_tool_outputs_slimmed,
      historicalImagesSlimmed: summary.historicalImagesSlimmed ?? summary.historical_images_slimmed,
    };
  },
  z.object({
    historicalToolOutputsSlimmed: z.number().int().nonnegative().optional(),
    historicalImagesSlimmed: z.number().int().nonnegative().optional(),
  }),
);

export const RequestLogSchema = z.object({
  requestedAt: z.string().datetime({ offset: true }),
  accountId: z.string().nullable(),
  planType: z.string().nullable().optional().default(null),
  apiKeyName: z.string().nullable(),
  apiKeyId: z.string().nullable().optional().default(null),
  requestId: z.string(),
  model: z.string(),
  transport: z.string().nullable().optional().default(null),
  serviceTier: z.string().nullable().optional().default(null),
  requestedServiceTier: z.string().nullable().optional().default(null),
  actualServiceTier: z.string().nullable().optional().default(null),
  status: z.string(),
  errorCode: z.string().nullable(),
  errorMessage: z.string().nullable(),
  tokens: z.number().nullable(),
  cachedInputTokens: z.number().nullable(),
  reasoningEffort: z.string().nullable(),
  costUsd: z.number().nullable(),
  latencyMs: z.number().nullable(),
  slimSummary: RequestLogSlimSummarySchema.nullable().optional(),
});

export const RequestLogsResponseSchema = z.object({
  requests: z.array(RequestLogSchema),
  total: z.number().int().nonnegative(),
  hasMore: z.boolean(),
});

export const RequestLogModelOptionSchema = z.object({
  model: z.string(),
  reasoningEffort: z.string().nullable(),
});

export const RequestLogApiKeyOptionSchema = z.object({
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
export type DashboardOverview = z.infer<typeof DashboardOverviewSchema>;
export type DashboardOverviewTimeframe = z.infer<typeof DashboardOverviewTimeframeSchema>;
export type TrendPoint = z.infer<typeof TrendPointSchema>;
export type MetricsTrends = z.infer<typeof MetricsTrendsSchema>;
export type UsageWindow = z.infer<typeof UsageWindowSchema>;
export type RequestLog = z.infer<typeof RequestLogSchema>;
export type RequestLogsResponse = z.infer<typeof RequestLogsResponseSchema>;
export type RequestLogFilterOptions = z.infer<typeof RequestLogFilterOptionsSchema>;
export type FilterState = z.infer<typeof FilterStateSchema>;
export type Depletion = z.infer<typeof DepletionSchema>;
