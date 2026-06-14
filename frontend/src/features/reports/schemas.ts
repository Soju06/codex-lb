import { z } from "zod";

export const DailyReportRowSchema = z.object({
  date: z.string(),
  requests: z.number(),
  inputTokens: z.number(),
  outputTokens: z.number(),
  cachedInputTokens: z.number(),
  costUsd: z.number(),
  activeAccounts: z.number(),
  errorCount: z.number(),
});

export const ModelCostEntrySchema = z.object({
  model: z.string(),
  costUsd: z.number(),
  percentage: z.number(),
});

export const AccountCostEntrySchema = z.object({
  accountId: z.string().nullable(),
  alias: z.string().nullable(),
  costUsd: z.number(),
  requests: z.number(),
});

export const ReportSummarySchema = z.object({
  totalCostUsd: z.number(),
  totalInputTokens: z.number(),
  totalOutputTokens: z.number(),
  totalCachedTokens: z.number(),
  totalRequests: z.number(),
  totalErrors: z.number(),
  activeAccounts: z.number(),
  avgCostPerDay: z.number(),
  avgRequestsPerDay: z.number(),
});

export const ReportComparisonPreviousSchema = z.object({
  totalCostUsd: z.number(),
  totalTokens: z.number(),
  totalRequests: z.number(),
});

export const ReportComparisonSchema = z.object({
  canCompare: z.boolean(),
  previous: ReportComparisonPreviousSchema,
});

export const ReportsResponseSchema = z.object({
  summary: ReportSummarySchema,
  comparison: ReportComparisonSchema,
  daily: z.array(DailyReportRowSchema),
  byModel: z.array(ModelCostEntrySchema),
  byAccount: z.array(AccountCostEntrySchema),
});

export type DailyReportRow = z.infer<typeof DailyReportRowSchema>;
export type ModelCostEntry = z.infer<typeof ModelCostEntrySchema>;
export type AccountCostEntry = z.infer<typeof AccountCostEntrySchema>;
export type ReportSummary = z.infer<typeof ReportSummarySchema>;
export type ReportComparison = z.infer<typeof ReportComparisonSchema>;
export type ReportsResponse = z.infer<typeof ReportsResponseSchema>;
