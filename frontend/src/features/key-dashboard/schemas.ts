import { z } from "zod";

import { RequestLogSchema, type RequestLog } from "@/features/dashboard/schemas";

export const KeyUsageSchema = z.object({
  request_count: z.number().int().nonnegative(),
  total_tokens: z.number().int().nonnegative(),
  cached_input_tokens: z.number().int().nonnegative(),
  total_cost_usd: z.number().nonnegative(),
  limits: z.array(z.unknown()),
  upstream_limits: z.array(z.unknown()).optional(),
  account_pool_usage: z.unknown().nullable().optional(),
});

const KeyDashboardCostBreakdownSchema = z.strictObject({
  inputUsd: z.number().nullable(),
  cachedInputUsd: z.number().nullable(),
  outputUsd: z.number().nullable(),
  totalUsd: z.number().nullable(),
});

export const KeyDashboardRequestLogSchema = z.strictObject({
  requestedAt: z.iso.datetime({ offset: true }),
  requestId: z.string(),
  requestKind: z.enum(["normal", "warmup", "limit_warmup", "prewarm", "compaction", "realtime_live"]),
  model: z.string(),
  transport: z.string().nullable(),
  upstreamTransport: z.string().nullable(),
  serviceTier: z.string().nullable(),
  requestedServiceTier: z.string().nullable(),
  actualServiceTier: z.string().nullable(),
  reasoningEffort: z.string().nullable(),
  status: z.string(),
  errorCode: z.string().nullable(),
  tokens: z.number().int().nullable(),
  inputTokens: z.number().int().nullable(),
  outputTokens: z.number().int().nullable(),
  outputTokensRaw: z.number().int().nullable(),
  reasoningTokens: z.number().int().nullable(),
  cachedInputTokens: z.number().int().nullable(),
  costUsd: z.number().nullable(),
  costBreakdown: KeyDashboardCostBreakdownSchema,
  latencyMs: z.number().int().nullable(),
  latencyFirstTokenMs: z.number().int().nullable(),
  latencyQueueMs: z.number().int().nullable(),
});

export const KeyDashboardRequestLogsResponseSchema = z.strictObject({
  requests: z.array(KeyDashboardRequestLogSchema),
  total: z.number().int().nonnegative(),
  hasMore: z.boolean(),
});

export type KeyUsage = z.infer<typeof KeyUsageSchema>;
export type KeyDashboardRequestLogsResponse = z.infer<typeof KeyDashboardRequestLogsResponseSchema>;

export function toDashboardRequestLog(
  request: z.infer<typeof KeyDashboardRequestLogSchema>,
): RequestLog {
  return RequestLogSchema.parse({
    ...request,
    accountId: null,
    planType: null,
    apiKeyId: null,
    apiKeyName: null,
    archiveRequestId: null,
    connectionRequestKind: null,
    source: null,
    modelSourceId: null,
    modelSourceKind: null,
    upstreamProxyRouteMode: null,
    upstreamProxyPoolId: null,
    upstreamProxyEndpointId: null,
    upstreamProxyFallbackUsed: null,
    upstreamProxyFailClosedReason: null,
    useragent: null,
    useragentGroup: null,
    clientIp: null,
    conversationId: null,
    errorMessage: null,
    failurePhase: null,
    failureDetail: null,
    failureExceptionType: null,
    upstreamStatusCode: null,
    upstreamErrorCode: null,
    bridgeStage: null,
  });
}
