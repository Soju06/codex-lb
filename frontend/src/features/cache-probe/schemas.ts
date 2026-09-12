import { z } from "zod";

export const CACHE_PROBE_ROLES = ["seed", "other"] as const;
export const CACHE_PROBE_CALL_STATUSES = ["hit", "miss", "error"] as const;
export const CACHE_PROBE_VERDICTS = [
  "cross_account_sharing",
  "no_cross_account_hit",
  "inconclusive",
] as const;

const CacheProbeRoleSchema = z.enum(CACHE_PROBE_ROLES);
const CacheProbeCallStatusSchema = z.enum(CACHE_PROBE_CALL_STATUSES);
const CacheProbeVerdictSchema = z.enum(CACHE_PROBE_VERDICTS);

export const CacheProbeAccountSchema = z.object({
  accountId: z.string().min(1),
  label: z.string().min(1),
});

export const CacheProbePressureSchema = z.object({
  underPressure: z.boolean(),
  reason: z.string().nullish(),
  detail: z.string().nullish(),
  selectableAccountCount: z.number().int().nonnegative(),
  eligibleAccountCount: z.number().int().nonnegative(),
  pressuredAccountCount: z.number().int().nonnegative(),
});

export const CacheProbePlanSchema = z.object({
  model: z.string().nullish(),
  seedAccount: CacheProbeAccountSchema.nullish(),
  availableOtherAccounts: z.array(CacheProbeAccountSchema).default([]),
  seedRepetitions: z.number().int().positive(),
  totalCalls: z.number().int().nonnegative(),
  estimatedInputTokensPerCall: z.number().int().nonnegative(),
  estimatedTotalInputTokens: z.number().int().nonnegative(),
  maxSeedRepetitions: z.number().int().positive(),
  maxOtherAccounts: z.number().int().positive(),
  pressure: CacheProbePressureSchema,
});

export const CacheProbeCallSchema = z.object({
  sequence: z.number().int().positive(),
  accountId: z.string().min(1),
  accountLabel: z.string().min(1),
  role: CacheProbeRoleSchema,
  status: CacheProbeCallStatusSchema,
  cacheHit: z.boolean(),
  inputTokens: z.number().int().nonnegative().nullish(),
  cachedTokens: z.number().int().nonnegative().nullish(),
  latencyMs: z.number().int().nonnegative(),
  errorCode: z.string().nullish(),
});

export const CacheProbeRunSchema = z.object({
  runId: z.string().min(1),
  model: z.string().min(1),
  startedAt: z.iso.datetime({ offset: true }),
  completedAt: z.iso.datetime({ offset: true }),
  seedAccount: CacheProbeAccountSchema,
  seedRepetitions: z.number().int().positive(),
  calls: z.array(CacheProbeCallSchema).default([]),
  seedHitCount: z.number().int().nonnegative(),
  seedCallCount: z.number().int().nonnegative(),
  otherHitCount: z.number().int().nonnegative(),
  otherCallCount: z.number().int().nonnegative(),
  crossAccountHit: z.boolean(),
  verdict: CacheProbeVerdictSchema,
});

export const CacheProbeRunRequestSchema = z.object({
  confirm: z.literal(true),
  seedRepetitions: z.number().int().positive(),
  otherAccountCount: z.number().int().positive(),
});

export type CacheProbeRole = z.infer<typeof CacheProbeRoleSchema>;
export type CacheProbeCallStatus = z.infer<typeof CacheProbeCallStatusSchema>;
export type CacheProbeVerdict = z.infer<typeof CacheProbeVerdictSchema>;
export type CacheProbeAccount = z.infer<typeof CacheProbeAccountSchema>;
export type CacheProbePressure = z.infer<typeof CacheProbePressureSchema>;
export type CacheProbePlan = z.infer<typeof CacheProbePlanSchema>;
export type CacheProbeCall = z.infer<typeof CacheProbeCallSchema>;
export type CacheProbeRun = z.infer<typeof CacheProbeRunSchema>;
export type CacheProbeRunRequest = z.infer<typeof CacheProbeRunRequestSchema>;
