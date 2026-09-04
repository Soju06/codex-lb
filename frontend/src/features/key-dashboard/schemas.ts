import { z } from "zod";

import { RequestLogSchema, type RequestLog } from "@/features/dashboard/schemas";

export const KeyUsageLimitSchema = z.strictObject({
  limit_type: z.string(),
  limit_window: z.string(),
  max_value: z.number().int().nonnegative(),
  current_value: z.number().int().nonnegative(),
  remaining_value: z.number().int().nonnegative(),
  model_filter: z.string().nullable(),
  reset_at: z.iso.datetime({ offset: true }),
  source: z.string(),
});

export const KeyUsageSchema = z.object({
  request_count: z.number().int().nonnegative(),
  total_tokens: z.number().int().nonnegative(),
  cached_input_tokens: z.number().int().nonnegative(),
  total_cost_usd: z.number().nonnegative(),
  limits: z.array(KeyUsageLimitSchema),
  upstream_limits: z.array(z.unknown()).optional(),
  account_pool_usage: z.unknown().nullable().optional(),
});

export const KeyDashboardProfileSchema = z.strictObject({
  name: z.string(),
  keyPrefix: z.string(),
  isActive: z.boolean(),
  createdAt: z.iso.datetime({ offset: true }),
  expiresAt: z.iso.datetime({ offset: true }).nullable(),
  lastUsedAt: z.iso.datetime({ offset: true }).nullable(),
  allowedModels: z.array(z.string()).nullable(),
  enforcedModel: z.string().nullable(),
  allowedReasoningEfforts: z.array(z.string()).nullable(),
  enforcedReasoningEffort: z.string().nullable(),
  enforcedServiceTier: z.string().nullable(),
  trafficClass: z.string(),
  transportPolicyOverride: z.string().nullable(),
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
export type KeyUsageLimit = z.infer<typeof KeyUsageLimitSchema>;
export type KeyDashboardProfile = z.infer<typeof KeyDashboardProfileSchema>;
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
