import { z } from "zod";

export const ModelSourceModelSchema = z.object({
  healthCheck: z.object({
    state: z.enum(["unknown", "healthy", "unhealthy", "slow", "stale"]),
    checkedAt: z.iso.datetime({ offset: true }).nullable(),
    expiresAt: z.iso.datetime({ offset: true }).nullable(),
    nextDueAt: z.iso.datetime({ offset: true }).nullable(),
    latencyMs: z.number().nullable(),
    firstTokenMs: z.number().nullable(),
    errorCode: z.string().nullable(),
    inputTokens: z.number().nullable(),
    outputTokens: z.number().nullable(),
    catalogVisible: z.boolean(),
  }).nullable().default(null),
  id: z.number(),
  sourceId: z.string(),
  model: z.string(),
  displayName: z.string().nullable().default(null),
  contextWindow: z.number().int().positive().nullable().default(null),
  maxOutputTokens: z.number().int().positive().nullable().default(null),
  supportsStreaming: z.boolean().default(true),
  supportsTools: z.boolean().default(false),
  supportsVision: z.boolean().default(false),
  inputPer1M: z.number().nullable().default(null),
  cachedInputPer1M: z.number().nullable().default(null),
  outputPer1M: z.number().nullable().default(null),
  audioPerMinute: z.number().nullable().default(null),
  rawMetadataJson: z.string().nullable().default(null),
  isEnabled: z.boolean().default(true),
  createdAt: z.iso.datetime({ offset: true }),
  updatedAt: z.iso.datetime({ offset: true }),
});

export const ModelSourceSchema = z.object({
  localTokenBudget: z.number().nullable().default(null),
  id: z.string(),
  name: z.string(),
  kind: z.string(),
  baseUrl: z.string(),
  isEnabled: z.boolean(),
  healthStatus: z.string(),
  companyStatus: z.object({
    inFlight: z.number().default(0),
    healthCheckSummary: z.object({
      enabledModels: z.number(),
      freshChecks: z.number(),
      visibleModels: z.number(),
      intervalSeconds: z.number(),
      timeoutSeconds: z.number(),
      freshnessSeconds: z.number(),
    }).nullable().default(null),
    health: z.enum(["unknown", "healthy", "degraded", "unavailable"]).default("unknown"),
    cooldownUntil: z.iso.datetime({ offset: true }).nullable().default(null),
    successes: z.number().default(0),
    failures: z.number().default(0),
    rateLimits: z.number().default(0),
    serverErrors: z.number().default(0),
    averageLatencyMs: z.number().nullable().default(null),
    budgetUsed: z.number().default(0),
    budgetExhausted: z.boolean().default(false),
    credentialCache: z.enum(["present", "unavailable"]),
    quotaStatus: z.literal("unknown"),
    remaining: z.null(),
    resetsAt: z.null(),
    observedUsage: z.object({
      since: z.iso.datetime({ offset: true }),
      requests: z.number(),
      requestsWithoutUsage: z.number(),
      inputTokens: z.number().nullable(),
      outputTokens: z.number().nullable(),
    }).nullable(),
  }).nullable().default(null),
  supportsChatCompletions: z.boolean(),
  supportsResponses: z.boolean(),
  supportsAudioTranscriptions: z.boolean().default(false),
  supportsEmbeddings: z.boolean().default(false),
  timeoutSeconds: z.number().int().positive().nullable().default(null),
  maxConcurrency: z.number().int().positive().nullable().default(null),
  createdAt: z.iso.datetime({ offset: true }),
  updatedAt: z.iso.datetime({ offset: true }),
  models: z.array(ModelSourceModelSchema).default([]),
});

export const ModelSourcesResponseSchema = z.object({
  sources: z.array(ModelSourceSchema).default([]),
});

export const ModelSourceModelInputSchema = z.object({
  model: z.string().min(1).max(255),
  displayName: z.string().max(255).nullable().optional(),
  contextWindow: z.number().int().positive().nullable().optional(),
  maxOutputTokens: z.number().int().positive().nullable().optional(),
  supportsStreaming: z.boolean().optional(),
  supportsTools: z.boolean().optional(),
  supportsVision: z.boolean().optional(),
  inputPer1M: z.number().nonnegative().nullable().optional(),
  cachedInputPer1M: z.number().nonnegative().nullable().optional(),
  outputPer1M: z.number().nonnegative().nullable().optional(),
  audioPerMinute: z.number().nonnegative().nullable().optional(),
  rawMetadataJson: z.string().nullable().optional(),
  isEnabled: z.boolean().optional(),
});

export const ModelSourceCreateRequestSchema = z.object({
  localTokenBudget: z.number().int().positive().nullable().optional(),
  kind: z.enum(["openai_compatible", "llmbox", "trae", "codebase_llm"]).optional(),
  name: z.string().min(1).max(128),
  baseUrl: z.string().min(1).max(2048),
  apiKey: z.string().min(1).nullable().optional(),
  supportsChatCompletions: z.boolean().optional(),
  supportsResponses: z.boolean().optional(),
  supportsAudioTranscriptions: z.boolean().optional(),
  supportsEmbeddings: z.boolean().optional(),
  timeoutSeconds: z.number().int().positive().nullable().optional(),
  maxConcurrency: z.number().int().positive().nullable().optional(),
  models: z.array(ModelSourceModelInputSchema).default([]),
});

export const ModelSourceUpdateRequestSchema = z.object({
  localTokenBudget: z.number().int().positive().nullable().optional(),
  name: z.string().min(1).max(128).optional(),
  baseUrl: z.string().min(1).max(2048).optional(),
  apiKey: z.string().min(1).nullable().optional(),
  isEnabled: z.boolean().optional(),
  supportsChatCompletions: z.boolean().optional(),
  supportsResponses: z.boolean().optional(),
  supportsAudioTranscriptions: z.boolean().optional(),
  supportsEmbeddings: z.boolean().optional(),
  timeoutSeconds: z.number().int().positive().nullable().optional(),
  maxConcurrency: z.number().int().positive().nullable().optional(),
  models: z.array(ModelSourceModelInputSchema).optional(),
});

export type ModelSource = z.infer<typeof ModelSourceSchema>;
export type ModelSourceModel = z.infer<typeof ModelSourceModelSchema>;
export type ModelSourceModelInput = z.infer<typeof ModelSourceModelInputSchema>;
export type ModelSourceCreateRequest = z.infer<typeof ModelSourceCreateRequestSchema>;
export type ModelSourceUpdateRequest = z.infer<typeof ModelSourceUpdateRequestSchema>;
