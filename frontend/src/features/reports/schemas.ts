import { z } from "zod";

const DailyReportRowSchema = z.object({
  date: z.string(),
  requests: z.number(),
  inputTokens: z.number(),
  outputTokens: z.number(),
  cachedInputTokens: z.number(),
  costUsd: z.number(),
  activeAccounts: z.number(),
  errorCount: z.number(),
});

const ModelCostEntrySchema = z.object({
  model: z.string(),
  costUsd: z.number(),
  requests: z.number(),
  percentage: z.number(),
});

const UseragentCostEntrySchema = z.object({
  useragent: z.string(),
  costUsd: z.number(),
  requests: z.number(),
  percentage: z.number(),
});

const AccountCostEntrySchema = z.object({
  accountId: z.string().nullable(),
  alias: z.string().nullable(),
  costUsd: z.number(),
  requests: z.number(),
});

const ReportSummarySchema = z.object({
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

const ReportComparisonPreviousSchema = z.object({
  totalCostUsd: z.number(),
  totalTokens: z.number(),
  totalRequests: z.number(),
});

const ReportComparisonSchema = z.object({
  canCompare: z.boolean(),
  previous: ReportComparisonPreviousSchema,
});

export const ReportsResponseSchema = z.object({
  summary: ReportSummarySchema,
  comparison: ReportComparisonSchema,
  daily: z.array(DailyReportRowSchema),
  byModel: z.array(ModelCostEntrySchema),
  byUseragent: z.array(UseragentCostEntrySchema),
  byAccount: z.array(AccountCostEntrySchema),
});

export type DailyReportRow = z.infer<typeof DailyReportRowSchema>;
export type ModelCostEntry = z.infer<typeof ModelCostEntrySchema>;
export type UseragentCostEntry = z.infer<typeof UseragentCostEntrySchema>;
export type AccountCostEntry = z.infer<typeof AccountCostEntrySchema>;
export type ReportSummary = z.infer<typeof ReportSummarySchema>;
export type ReportComparison = z.infer<typeof ReportComparisonSchema>;
export type ReportsResponse = z.infer<typeof ReportsResponseSchema>;
