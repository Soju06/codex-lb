import { z } from "zod";

export const AccountUsageSchema = z.object({
  primaryRemainingPercent: z.number().nullable(),
  secondaryRemainingPercent: z.number().nullable(),
});

export const AccountTokenStatusSchema = z.object({
  expiresAt: z.string().datetime({ offset: true }).nullable().optional(),
  state: z.string().nullable().optional(),
});

export const AccountAuthSchema = z.object({
  access: AccountTokenStatusSchema.nullable().optional(),
  refresh: AccountTokenStatusSchema.nullable().optional(),
  idToken: AccountTokenStatusSchema.nullable().optional(),
});

export const AccountSummarySchema = z.object({
  accountId: z.string(),
  email: z.string(),
  displayName: z.string(),
  planType: z.string(),
  status: z.string(),
  usage: AccountUsageSchema.nullable().optional(),
  resetAtPrimary: z.string().datetime({ offset: true }).nullable().optional(),
  resetAtSecondary: z.string().datetime({ offset: true }).nullable().optional(),
  auth: AccountAuthSchema.nullable().optional(),
});

export const UsageHistoryItemSchema = z.object({
  accountId: z.string(),
  remainingPercentAvg: z.number(),
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

export const UsageCostSchema = z.object({
  currency: z.string(),
  totalUsd7d: z.number(),
});

export const DashboardMetricsSchema = z.object({
  requests7d: z.number().nullable(),
  tokensSecondaryWindow: z.number().nullable(),
  cachedTokensSecondaryWindow: z.number().nullable(),
  errorRate7d: z.number().nullable(),
  topError: z.string().nullable(),
});

export const DashboardOverviewSchema = z.object({
  lastSyncAt: z.string().datetime({ offset: true }).nullable(),
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
});

export const RequestLogSchema = z.object({
  requestedAt: z.string().datetime({ offset: true }),
  accountId: z.string(),
  requestId: z.string(),
  model: z.string(),
  status: z.string(),
  errorCode: z.string().nullable(),
  errorMessage: z.string().nullable(),
  tokens: z.number().nullable(),
  cachedInputTokens: z.number().nullable(),
  reasoningEffort: z.string().nullable(),
  costUsd: z.number().nullable(),
  latencyMs: z.number().nullable(),
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

export const RequestLogFilterOptionsSchema = z.object({
  accountIds: z.array(z.string()),
  modelOptions: z.array(RequestLogModelOptionSchema),
  statuses: z.array(z.string()),
});

export const FilterStateSchema = z.object({
  search: z.string(),
  timeframe: z.enum(["all", "1h", "24h", "7d"]),
  accountIds: z.array(z.string()),
  modelOptions: z.array(z.string()),
  statuses: z.array(z.string()),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
});

export type AccountSummary = z.infer<typeof AccountSummarySchema>;
export type DashboardMetrics = z.infer<typeof DashboardMetricsSchema>;
export type DashboardOverview = z.infer<typeof DashboardOverviewSchema>;
export type UsageWindow = z.infer<typeof UsageWindowSchema>;
export type RequestLog = z.infer<typeof RequestLogSchema>;
export type RequestLogsResponse = z.infer<typeof RequestLogsResponseSchema>;
export type RequestLogFilterOptions = z.infer<typeof RequestLogFilterOptionsSchema>;
export type FilterState = z.infer<typeof FilterStateSchema>;
