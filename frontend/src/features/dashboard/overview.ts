import type { QuotaWindowKey } from "@/features/accounts/overview";
import type { AccountSummary, MetricsTrends, RequestLog } from "@/features/dashboard/schemas";

/** `status` query values per filter; Errors lists every visible non-`ok` status the backend normalizes to. */
export const REQUEST_STATUS_FILTERS = {
  all: [],
  success: ["ok"],
  errors: ["error", "rate_limit", "quota", "cancelled"],
} as const satisfies Record<string, readonly string[]>;

export type RequestStatusFilter = keyof typeof REQUEST_STATUS_FILTERS;

export type RequestLogSummary = {
  count: number;
  errorCount: number;
  /** Share of non-`ok` rows, 0–1; null when nothing is loaded. */
  errorShare: number | null;
  avgLatencyMs: number | null;
  p95LatencyMs: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
  /** Known cached input over known input, 0–1; null when either side is unknown or input is zero. */
  cacheShare: number | null;
};

function sumKnown(values: Array<number | null | undefined>): number | null {
  const known = values.filter((value): value is number => value != null);
  return known.length > 0 ? known.reduce((total, value) => total + value, 0) : null;
}

export function summarizeRequestLogs(rows: RequestLog[]): RequestLogSummary {
  const errorCount = rows.filter((row) => row.status !== "ok").length;
  const latencies = rows
    .map((row) => row.latencyMs)
    .filter((value): value is number => value != null)
    .sort((a, b) => a - b);
  const inputTokens = sumKnown(rows.map((row) => row.inputTokens));
  const cachedTokens = sumKnown(rows.map((row) => row.cachedInputTokens));
  return {
    count: rows.length,
    errorCount,
    errorShare: rows.length > 0 ? errorCount / rows.length : null,
    avgLatencyMs: latencies.length > 0 ? latencies.reduce((total, value) => total + value, 0) / latencies.length : null,
    // Nearest rank: the smallest value with at least 95% of values at or below it.
    p95LatencyMs: latencies.length > 0 ? latencies[Math.ceil(0.95 * latencies.length) - 1] : null,
    inputTokens,
    outputTokens: sumKnown(rows.map((row) => row.outputTokens)),
    // Clamped like the dashboard's cached-token meta, in case a log reports more cached than input tokens.
    cacheShare: inputTokens && cachedTokens !== null ? Math.min(1, cachedTokens / inputTokens) : null,
  };
}

export type QuotaColumn = {
  accountId: string;
  usedPercent: number;
  remainingPercent: number;
  /** Filled cells out of ten, rounded up so any use shows. */
  filled: number;
};

/** One column per account with a known remaining percent, most used first. */
export function buildQuotaColumns(accounts: AccountSummary[], key: QuotaWindowKey): QuotaColumn[] {
  const columns: QuotaColumn[] = [];
  for (const account of accounts) {
    const remaining = account.usage?.[`${key}RemainingPercent`];
    if (remaining == null || !Number.isFinite(remaining)) {
      continue;
    }
    const remainingPercent = Math.min(100, Math.max(0, remaining));
    const usedPercent = 100 - remainingPercent;
    columns.push({ accountId: account.accountId, usedPercent, remainingPercent, filled: Math.ceil(usedPercent / 10) });
  }
  return columns.sort((a, b) => b.usedPercent - a.usedPercent);
}

export type ActivityPoint = { t: string; requests: number; errors: number; tokens: number; errorRate: number };

/** Trend arrays share the same buckets; errors are derived because the payload carries only the rate. */
export function buildActivitySeries(trends: MetricsTrends): ActivityPoint[] {
  return trends.requests.map((point, index) => {
    const errorRate = trends.errorRate[index]?.v ?? 0;
    return {
      t: point.t,
      requests: point.v,
      errors: Math.round(point.v * errorRate),
      tokens: trends.tokens[index]?.v ?? 0,
      errorRate,
    };
  });
}
