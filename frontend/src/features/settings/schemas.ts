import { z } from "zod";

import { LocalLoginPolicySchema, StrictLocalLoginPolicySchema } from "@/features/auth/schemas";

const RoutingStrategySchema = z.enum([
  "usage_weighted",
  "round_robin",
  "capacity_weighted",
  "sequential_drain",
  "reset_drain",
  "single_account",
  "relative_availability",
  "fill_first",
]);
const UpstreamStreamTransportSchema = z.enum([
  "auto",
  "http",
  "websocket",
]);
const HttpDownstreamTransportPolicySchema = z.enum([
  "smart",
  "always_http",
  "always_websocket",
  "pinned",
]);
const LimitWarmupWindowsSchema = z.enum([
  "primary",
  "secondary",
  "both",
]);
const AdditionalQuotaRoutingPolicySchema = z.enum([
  "inherit",
  "normal",
  "burn_first",
  "preserve",
]);
const SettingScalarSchema = z.union([z.number(), z.string(), z.boolean()]);

// Where an inheritable setting's effective value comes from: the dashboard
// column, the environment (column NULL, env differs from the code default) or
// the code default. Keyed by the backend setting name (snake_case).
export const SettingProvenanceSchema = z.object({
  source: z.enum(["dashboard", "env", "default"]),
  envValue: SettingScalarSchema.nullable().optional().default(null),
  default: SettingScalarSchema.nullable().optional().default(null),
});

const AdditionalQuotaPolicySchema = z.object({
  quotaKey: z.string(),
  displayLabel: z.string(),
  routingPolicy: AdditionalQuotaRoutingPolicySchema,
  modelIds: z.array(z.string()).optional().default([]),
});
const LimitWarmupModelSchema = z.string().min(1).max(128);
const LimitWarmupPromptSchema = z.string().min(1).max(512);
const WeeklyPaceWorkingDaysValueSchema = z.string().regex(/^[0-6](,[0-6])*$/);
const WeeklyPaceWorkingDaysSchema = WeeklyPaceWorkingDaysValueSchema.default("0,1,2,3,4,5,6");
const WeeklyPaceSmoothingMinutesSchema = z.union([
  z.literal(15),
  z.literal(30),
  z.literal(60),
  z.literal(120),
  z.literal(240),
]);

export const DashboardSettingsSchema = z
  .object({
    stickyThreadsEnabled: z.boolean(),
    upstreamStreamTransport:
      UpstreamStreamTransportSchema.optional().default("auto"),
    prohibitFastMode: z.boolean().optional().default(false),
    httpDownstreamTransportPolicy:
      HttpDownstreamTransportPolicySchema.optional().default("smart"),
    upstreamProxyRoutingEnabled: z.boolean().optional().default(false),
    upstreamProxyDefaultPoolId: z.string().nullable().optional().default(null),
    preferEarlierResetAccounts: z.boolean(),
    preferEarlierResetWindow: z.enum(["primary", "secondary"]).optional().default("secondary"),
    showResetCreditBadges: z.boolean().optional().default(true),
    autoRedeemResetCreditsBeforeExpiry: z.boolean().optional().default(false),
    showResetCreditExpiryBadge: z.boolean().optional().default(true),
    routingStrategy: RoutingStrategySchema.optional().default("usage_weighted"),
    relativeAvailabilityPower: z.number().positive().optional().default(2),
    relativeAvailabilityTopK: z
      .number()
      .int()
      .min(1)
      .max(20)
      .optional()
      .default(5),
    singleAccountId: z.string().nullable().optional().default(null),
    subscriptionOverflowSourceId: z.string().nullable().optional().default(null),
    subscriptionOverflowDrainUntil: z.iso.datetime({ offset: true }).nullable().optional().default(null),
    // Derived by the backend from the deadline: the date every conversation
    // pinned to the cleared source has expired by (clear time + 7 days).
    subscriptionOverflowPinsExpireBy: z.iso.datetime({ offset: true }).nullable().optional().default(null),
    proxyAccountResponseCreateLimit: z.number().int().min(0).optional().default(4),
    proxyAccountResponseCreateLimitEnvironmentValue: z.number().int().min(0).optional().default(4),
    proxyAccountResponseCreateLimitOverride: z.number().int().min(0).nullable().optional().default(null),
    proxyAccountStreamLimit: z.number().int().min(0).optional().default(8),
    proxyAccountStreamLimitEnvironmentValue: z.number().int().min(0).optional().default(8),
    proxyAccountStreamLimitOverride: z.number().int().min(0).nullable().optional().default(null),
    proxyAccountStreamRecoveryReserve: z.number().int().min(0).optional().default(1),
    proxyAccountStreamRecoveryReserveEnvironmentValue: z.number().int().min(0).optional().default(1),
    proxyAccountStreamRecoveryReserveOverride: z.number().int().min(0).nullable().optional().default(null),
    proxyApiKeyFairShareCongestionThresholdPct: z.number().int().min(0).max(100).optional().default(0),
    proxyApiKeyFairShareCongestionThresholdPctEnvironmentValue: z
      .number()
      .int()
      .min(0)
      .max(100)
      .optional()
      .default(0),
    proxyApiKeyFairShareCongestionThresholdPctOverride: z
      .number()
      .int()
      .min(0)
      .max(100)
      .nullable()
      .optional()
      .default(null),
    // C2-2 routing/overload: effective values; `provenance[<snake_name>]`
    // says whether the dashboard, the environment or the default owns each.
    proxyOverloadIsolationSeconds: z.number().int().min(0).optional().default(1800),
    proxyAccountErrorRateWeightingEnabled: z.boolean().optional().default(true),
    // Response side keeps the environment bound only (an inherited env value
    // above the dashboard write cap of 100 must still parse).
    proxyAccountInflightPenaltyPct: z.number().min(0).optional().default(2.5),
    proxyAccountLeaseTokenWeight: z.number().min(0).optional().default(1),
    proxyAccountLeaseTtlSeconds: z.number().positive().optional().default(900),
    openaiCacheAffinityMaxAgeSeconds: z
      .number()
      .int()
      .positive()
      .optional()
      .default(300),
    dashboardSessionTtlSeconds: z
      .number()
      .int()
      .min(3600)
      .optional()
      .default(31536000),
    stickyReallocationBudgetThresholdPct: z.number().min(0).max(100).optional(),
    stickyReallocationPrimaryBudgetThresholdPct: z.number().min(0).max(100).optional(),
    stickyReallocationSecondaryBudgetThresholdPct: z.number().min(0).max(100).optional(),
    additionalQuotaRoutingPolicies: z
      .record(z.string(), AdditionalQuotaRoutingPolicySchema)
      .optional(),
    additionalQuotaPolicies: z.array(AdditionalQuotaPolicySchema).optional().default([]),
    warmupModel: z.string().trim().min(1).optional().default("gpt-5.4-mini"),
    importWithoutOverwrite: z.boolean(),
    totpRequiredOnLogin: z.boolean(),
    // Defaults cover a mixed-version rollout against an older backend.
    totpRequiredForAdminRole: z.boolean().optional().default(false),
    // Who may still sign in with a local password; database only, never an env var.
    localLoginPolicy: LocalLoginPolicySchema.optional().default("enabled"),
    usersWithoutTotpCount: z.number().int().min(0).optional().default(0),
    adminsWithoutTotpCount: z.number().int().min(0).optional().default(0),
    apiKeyAuthEnabled: z.boolean(),
    hideUpstreamQuotaFromApiKeys: z.boolean().optional().default(false),
    limitWarmupEnabled: z.boolean().optional().default(false),
    limitWarmupWindows: LimitWarmupWindowsSchema.optional().default("both"),
    limitWarmupModel: LimitWarmupModelSchema.optional().default("auto"),
    limitWarmupPrompt: LimitWarmupPromptSchema.optional().default("Say OK."),
    limitWarmupCooldownSeconds: z.number().int().min(60).optional().default(3600),
    limitWarmupExhaustedThresholdPercent: z
      .number()
      .positive()
      .max(100)
      .optional()
      .default(99),
    limitWarmupIdleThresholdPercent: z
      .number()
      .positive()
      .max(100)
      .optional()
      .default(1),
    limitWarmupMinAvailablePercent: z
      .number()
      .positive()
      .max(100)
      .optional()
      .default(100),
    weeklyPaceWorkingDays: WeeklyPaceWorkingDaysSchema,
    weeklyPaceSmoothingMinutes: WeeklyPaceSmoothingMinutesSchema.optional().default(30),
    guestAccessEnabled: z.boolean().optional().default(false),
    guestPasswordConfigured: z.boolean().optional().default(false),
    limitWarmupStaggeredIdleEnabled: z.boolean().optional().default(false),
    requestLogRetentionDays: z.number().int().min(0).max(3650).optional().default(0),
    usageHistoryRetentionDays: z.number().int().min(0).max(3650).optional().default(0),
    requestLogRetentionOverrideDays: z.number().int().min(0).max(3650).nullable().optional().default(null),
    usageHistoryRetentionOverrideDays: z.number().int().min(0).max(3650).nullable().optional().default(null),
    // C2-1 timeouts: effective values (dashboard column, else environment,
    // else code default); `provenance[<snake_name>]` says which. Optional with
    // the code defaults so older backends still parse.
    // Unbounded like the backend response: an environment value the server
    // accepts must still render (the update schema below keeps the bounds).
    upstreamConnectTimeoutSeconds: z.number().optional().default(8),
    proxyRequestBudgetSeconds: z.number().optional().default(600),
    compactRequestBudgetSeconds: z.number().optional().default(180),
    transcriptionRequestBudgetSeconds: z.number().optional().default(120),
    streamIdleTimeoutSeconds: z.number().optional().default(7200),
    proxyDownstreamWebsocketIdleTimeoutSeconds: z.number().optional().default(120),
    sseKeepaliveIntervalSeconds: z.number().optional().default(10),
    // M1 stream/bridge budgets: effective values, same contract as the C2-1
    // timeouts above (optional with the code defaults, unbounded).
    httpResponsesStreamRequestBudgetSeconds: z.number().optional().default(7200),
    httpResponsesSessionBridgeRequestBudgetSeconds: z.number().optional().default(7200),
    // Optional so responses from backends that predate provenance still parse.
    provenance: z.record(z.string(), SettingProvenanceSchema).optional(),
    // C2-3 resilience toggles: effective values; `provenance[<snake_name>]`
    // says whether each comes from the dashboard, the environment or the default.
    softDrainEnabled: z.boolean().optional().default(true),
    deterministicFailoverEnabled: z.boolean().optional().default(true),
    circuitBreakerEnabled: z.boolean().optional().default(false),
    // M3 codex prewarm: effective value; `provenance[<snake_name>]` says
    // whether the dashboard, the environment or the default owns it.
    httpResponsesSessionBridgeCodexPrewarmEnabled: z.boolean().optional().default(false),
    // M2 background jobs: effective values; `provenance[<snake_name>]` says
    // which layer supplied each. `authGuardianBlockedByTopology` is true when a
    // multi-replica ring without leader election keeps the guardian idle.
    authGuardianEnabled: z.boolean().optional().default(true),
    authGuardianBlockedByTopology: z.boolean().optional().default(false),
    automationsSchedulerEnabled: z.boolean().optional().default(true),
    rateLimitResetCreditsRefreshEnabled: z.boolean().optional().default(true),
    // M5 conversation archive: effective toggle (`provenance.conversation_archive_enabled`
    // says which layer it comes from) and this replica's read-only archive
    // directory (environment-only; null for read-only guests / older backends).
    conversationArchiveEnabled: z.boolean().optional().default(false),
    conversationArchiveDir: z.string().nullable().optional().default(null),
    // end M5 conversation archive
    // R2 spool retention: effective retention of the durable HTTP bridge
    // operation spool (raw request payloads + response events) and the floor
    // the API enforces, so the card can mirror the check before saving.
    // `provenance.http_responses_session_bridge_operation_spool_retention_seconds`
    // says which layer owns the value.
    httpResponsesSessionBridgeOperationSpoolRetentionSeconds: z.number().optional().default(604800),
    httpResponsesSessionBridgeOperationSpoolRetentionFloorSeconds: z.number().optional().default(0),
    // end R2 spool retention
    version: z.number().int().min(1).optional(),
  })
  .transform((settings) => {
    const legacyProvided = settings.stickyReallocationBudgetThresholdPct !== undefined;
    const primaryProvided = settings.stickyReallocationPrimaryBudgetThresholdPct !== undefined;
    const secondaryProvided = settings.stickyReallocationSecondaryBudgetThresholdPct !== undefined;
    const primaryThreshold =
      settings.stickyReallocationPrimaryBudgetThresholdPct ??
      settings.stickyReallocationBudgetThresholdPct ??
      95;
    return {
      ...settings,
      stickyReallocationBudgetThresholdPct:
        settings.stickyReallocationBudgetThresholdPct ?? primaryThreshold,
      stickyReallocationPrimaryBudgetThresholdPct: primaryThreshold,
      stickyReallocationSecondaryBudgetThresholdPct:
        settings.stickyReallocationSecondaryBudgetThresholdPct ??
        settings.stickyReallocationBudgetThresholdPct ??
        100,
      __stickyReallocationBudgetThresholdPctProvided: legacyProvided,
      __stickyReallocationPrimaryBudgetThresholdPctProvided: primaryProvided,
      __stickyReallocationSecondaryBudgetThresholdPctProvided: secondaryProvided,
    };
  });

export const SettingsUpdateRequestSchema = z
  .object({
    expectedVersion: z.number().int().min(1).optional(),
    stickyThreadsEnabled: z.boolean().optional(),
    upstreamStreamTransport: UpstreamStreamTransportSchema.optional(),
    prohibitFastMode: z.boolean().optional(),
    httpDownstreamTransportPolicy: HttpDownstreamTransportPolicySchema.optional(),
    upstreamProxyRoutingEnabled: z.boolean().optional(),
    upstreamProxyDefaultPoolId: z.string().nullable().optional(),
    preferEarlierResetAccounts: z.boolean().optional(),
    preferEarlierResetWindow: z.enum(["primary", "secondary"]).optional(),
    showResetCreditBadges: z.boolean().optional(),
    autoRedeemResetCreditsBeforeExpiry: z.boolean().optional(),
    showResetCreditExpiryBadge: z.boolean().optional(),
    routingStrategy: RoutingStrategySchema.optional(),
    relativeAvailabilityPower: z.number().positive().optional(),
    relativeAvailabilityTopK: z.number().int().min(1).max(20).optional(),
    singleAccountId: z.string().nullable().optional(),
    // Tri-state: absent = unchanged, null = off (arms the drain deadline),
    // value = designate. The drain deadline itself is read-only.
    subscriptionOverflowSourceId: z.string().nullable().optional(),
    proxyAccountResponseCreateLimit: z.number().int().min(0).nullable().optional(),
    proxyAccountStreamLimit: z.number().int().min(0).nullable().optional(),
    proxyAccountStreamRecoveryReserve: z.number().int().min(0).nullable().optional(),
    proxyApiKeyFairShareCongestionThresholdPct: z.number().int().min(0).max(100).nullable().optional(),
    // C2-2 routing/overload: tri-state like the caps (absent = unchanged,
    // null = inherit, value = store); bounds mirror the backend schema.
    proxyOverloadIsolationSeconds: z.number().int().min(0).nullable().optional(),
    proxyAccountErrorRateWeightingEnabled: z.boolean().nullable().optional(),
    proxyAccountInflightPenaltyPct: z.number().min(0).max(100).nullable().optional(),
    proxyAccountLeaseTokenWeight: z.number().min(0).nullable().optional(),
    proxyAccountLeaseTtlSeconds: z.number().positive().nullable().optional(),
    openaiCacheAffinityMaxAgeSeconds: z.number().int().positive().optional(),
    dashboardSessionTtlSeconds: z.number().int().min(3600).optional(),
    stickyReallocationBudgetThresholdPct: z.number().min(0).max(100).optional(),
    stickyReallocationPrimaryBudgetThresholdPct: z.number().min(0).max(100).optional(),
    stickyReallocationSecondaryBudgetThresholdPct: z.number().min(0).max(100).optional(),
    additionalQuotaRoutingPolicies: z
      .record(z.string(), AdditionalQuotaRoutingPolicySchema)
      .optional(),
    warmupModel: z.string().trim().min(1).optional(),
    importWithoutOverwrite: z.boolean().optional(),
    totpRequiredOnLogin: z.boolean().optional(),
    totpRequiredForAdminRole: z.boolean().optional(),
    // Strict, unlike the response field: `updateSettings` parses an `unknown`
    // payload, so a fallback here would turn a typo into a policy relaxation
    // the backend would happily accept.
    localLoginPolicy: StrictLocalLoginPolicySchema.optional(),
    apiKeyAuthEnabled: z.boolean().optional(),
    hideUpstreamQuotaFromApiKeys: z.boolean().optional(),
    limitWarmupEnabled: z.boolean().optional(),
    limitWarmupWindows: LimitWarmupWindowsSchema.optional(),
    limitWarmupModel: LimitWarmupModelSchema.optional(),
    limitWarmupPrompt: LimitWarmupPromptSchema.optional(),
    limitWarmupCooldownSeconds: z.number().int().min(60).optional(),
    limitWarmupExhaustedThresholdPercent: z.number().positive().max(100).optional(),
    limitWarmupIdleThresholdPercent: z.number().positive().max(100).optional(),
    limitWarmupMinAvailablePercent: z.number().positive().max(100).optional(),
    weeklyPaceWorkingDays: WeeklyPaceWorkingDaysValueSchema.optional(),
    weeklyPaceSmoothingMinutes: WeeklyPaceSmoothingMinutesSchema.optional(),
    guestAccessEnabled: z.boolean().optional(),
    limitWarmupStaggeredIdleEnabled: z.boolean().optional(),
    // Tri-state overrides: absent = unchanged, null = clear (inherit env
    // alias), value = store the override.
    requestLogRetentionOverrideDays: z.number().int().min(0).max(3650).nullable().optional(),
    usageHistoryRetentionOverrideDays: z.number().int().min(0).max(3650).nullable().optional(),
    // C2-3 resilience toggles: tri-state (omitted = unchanged, null = reset to
    // inherited, boolean = dashboard value).
    softDrainEnabled: z.boolean().nullable().optional(),
    deterministicFailoverEnabled: z.boolean().nullable().optional(),
    circuitBreakerEnabled: z.boolean().nullable().optional(),
    // M3 codex prewarm: tri-state (omitted = unchanged, null = reset to
    // inherited, boolean = dashboard value).
    httpResponsesSessionBridgeCodexPrewarmEnabled: z.boolean().nullable().optional(),
    // M2 background jobs: tri-state like the resilience toggles.
    authGuardianEnabled: z.boolean().nullable().optional(),
    automationsSchedulerEnabled: z.boolean().nullable().optional(),
    rateLimitResetCreditsRefreshEnabled: z.boolean().nullable().optional(),
    // M5 conversation archive: tri-state (omitted = unchanged, null = reset to
    // inherited, boolean = dashboard value). The card only sends `true` after
    // the confirmation dialog.
    conversationArchiveEnabled: z.boolean().nullable().optional(),
    // end M5 conversation archive
    // R2 spool retention: tri-state (omitted = unchanged, null = reset to
    // inherited, value = dashboard value in seconds). The replay floor is
    // enforced by the backend against the effective values.
    httpResponsesSessionBridgeOperationSpoolRetentionSeconds: z
      .number()
      .positive()
      .max(315360000)
      .nullable()
      .optional(),
    // end R2 spool retention
    // C2-1 timeouts, tri-state like the caps: absent = unchanged, null = clear
    // (inherit environment / default), value = store. Cross-field invariants
    // are enforced by the backend against the effective values.
    upstreamConnectTimeoutSeconds: z.number().positive().max(86400).nullable().optional(),
    proxyRequestBudgetSeconds: z.number().positive().max(86400).nullable().optional(),
    compactRequestBudgetSeconds: z.number().positive().max(86400).nullable().optional(),
    transcriptionRequestBudgetSeconds: z.number().positive().max(86400).nullable().optional(),
    streamIdleTimeoutSeconds: z.number().positive().max(86400).nullable().optional(),
    proxyDownstreamWebsocketIdleTimeoutSeconds: z.number().positive().max(86400).nullable().optional(),
    sseKeepaliveIntervalSeconds: z.number().min(0).max(86400).nullable().optional(),
    // M1 stream/bridge budgets: tri-state like the C2-1 timeouts.
    httpResponsesStreamRequestBudgetSeconds: z.number().positive().max(86400).nullable().optional(),
    httpResponsesSessionBridgeRequestBudgetSeconds: z.number().positive().max(86400).nullable().optional(),
  })
  .superRefine((settings, ctx) => {
    if (
      settings.proxyAccountStreamLimit !== undefined &&
      settings.proxyAccountStreamLimit !== null &&
      settings.proxyAccountStreamLimit > 0 &&
      settings.proxyAccountStreamRecoveryReserve !== undefined &&
      settings.proxyAccountStreamRecoveryReserve !== null &&
      settings.proxyAccountStreamRecoveryReserve > settings.proxyAccountStreamLimit
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["proxyAccountStreamRecoveryReserve"],
        message: "proxyAccountStreamRecoveryReserve must not exceed proxyAccountStreamLimit",
      });
    }
    if (
      settings.requestLogRetentionOverrideDays !== undefined &&
      settings.requestLogRetentionOverrideDays !== null &&
      settings.requestLogRetentionOverrideDays !== 0 &&
      settings.requestLogRetentionOverrideDays < 30
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["requestLogRetentionOverrideDays"],
        message: "request_log_retention_override_days must be 0 (disabled) or >= 30",
      });
    }
    if (
      settings.usageHistoryRetentionOverrideDays !== undefined &&
      settings.usageHistoryRetentionOverrideDays !== null &&
      settings.usageHistoryRetentionOverrideDays !== 0 &&
      settings.usageHistoryRetentionOverrideDays < 45
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["usageHistoryRetentionOverrideDays"],
        message: "usage_history_retention_override_days must be 0 (disabled) or >= 45",
      });
    }
  });

type ParsedDashboardSettings = z.infer<typeof DashboardSettingsSchema>;
type StickyThresholdPresenceFlags = Pick<
  ParsedDashboardSettings,
  | "__stickyReallocationBudgetThresholdPctProvided"
  | "__stickyReallocationPrimaryBudgetThresholdPctProvided"
  | "__stickyReallocationSecondaryBudgetThresholdPctProvided"
>;
type StickyThresholdValues = Pick<
  ParsedDashboardSettings,
  | "stickyReallocationBudgetThresholdPct"
  | "stickyReallocationPrimaryBudgetThresholdPct"
  | "stickyReallocationSecondaryBudgetThresholdPct"
>;

export type DashboardSettings = Omit<
  ParsedDashboardSettings,
  keyof StickyThresholdPresenceFlags | keyof StickyThresholdValues
> &
  Partial<StickyThresholdPresenceFlags> &
  Partial<StickyThresholdValues>;
export type SettingsUpdateRequest = z.infer<typeof SettingsUpdateRequestSchema>;
export type SettingProvenance = z.infer<typeof SettingProvenanceSchema>;
export type AdditionalQuotaRoutingPolicy = z.infer<typeof AdditionalQuotaRoutingPolicySchema>;

export const UpstreamProxyEndpointSchema = z.object({
  id: z.string(),
  name: z.string(),
  scheme: z.enum(["http", "https", "socks5", "socks5h"]),
  host: z.string(),
  port: z.number().int(),
  username: z.string().nullable().optional(),
  isActive: z.boolean(),
  // Credentials cross the LB-to-proxy hop unencrypted (http/socks5 with a
  // username or password); the endpoint list renders a warning.
  plaintextCredentials: z.boolean().optional().default(false),
});

export const UpstreamProxyEndpointCreateRequestSchema = z.object({
  name: z.string().trim().min(1).max(128),
  scheme: z.enum(["http", "https", "socks5", "socks5h"]),
  host: z.string().trim().min(1).max(255),
  port: z.number().int().min(1).max(65535),
  username: z.string().trim().max(255).nullable().optional(),
  password: z.string().max(1024).nullable().optional(),
  isActive: z.boolean().optional().default(true),
});

export const UpstreamProxyEndpointTestResponseSchema = z.object({
  endpointId: z.string(),
  ok: z.boolean(),
  statusCode: z.number().int().nullable().optional(),
  elapsedMs: z.number().int().nullable().optional(),
  error: z.string().nullable().optional(),
});

export const UpstreamProxyPoolSchema = z.object({
  id: z.string(),
  name: z.string(),
  isActive: z.boolean(),
  endpointIds: z.array(z.string()),
});

export const UpstreamProxyPoolCreateRequestSchema = z.object({
  name: z.string().trim().min(1).max(128),
  endpointIds: z.array(z.string()).default([]),
  isActive: z.boolean().optional().default(true),
});

export const UpstreamProxyPoolMemberRequestSchema = z.object({
  endpointId: z.string().min(1),
  sortOrder: z.number().int().optional().default(0),
  weight: z.number().int().min(1).optional().default(1),
  isActive: z.boolean().optional().default(true),
});

export const AccountProxyBindingSchema = z.object({
  accountId: z.string(),
  poolId: z.string(),
  isActive: z.boolean(),
});

export const AccountProxyBindingRequestSchema = z.object({
  poolId: z.string().min(1),
  isActive: z.boolean().optional().default(true),
});

export const UpstreamProxyAdminSchema = z.object({
  routingEnabled: z.boolean(),
  defaultPoolId: z.string().nullable(),
  endpoints: z.array(UpstreamProxyEndpointSchema),
  pools: z.array(UpstreamProxyPoolSchema),
  bindings: z.array(AccountProxyBindingSchema),
});

// M4 model catalogue: per-model context window overrides. `source` is
// "dashboard" when a dashboard row exists for the slug and "env" when only the
// CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES entry applies; `envValue` is that
// entry (null when the environment has none).
export const ModelContextWindowOverrideSchema = z.object({
  slug: z.string().min(1),
  contextWindow: z.number().int().positive(),
  source: z.enum(["dashboard", "env"]),
  envValue: z.number().int().positive().nullable().optional().default(null),
});

export const ModelContextWindowOverridesSchema = z.object({
  overrides: z.array(ModelContextWindowOverrideSchema),
});

export const ModelContextWindowOverrideUpsertRequestSchema = z.object({
  contextWindow: z.number().int().positive(),
});
// end M4 model catalogue

export const TelemetryConsentStateSchema = z.enum(["undecided", "enabled", "disabled"]);
export const TelemetryConsentSourceSchema = z.enum(["env", "persisted", "default"]);

// Wire-format (snake_case) mirror of app/modules/telemetry/schemas.py. Every
// object is strict so backend drift (renamed, added, or removed fields) fails
// schema parsing instead of passing silently.
const TelemetryDeploymentSnapshotSchema = z.strictObject({
  method: z.enum(["docker", "k8s", "pip", "bare"]),
  db_backend: z.enum(["sqlite", "postgres"]),
  db_size_bucket: z.enum(["unknown", "<100MB", "100MB-1GB", "1-5GB", "5-10GB", "10-50GB", "50GB+"]),
  replicas: z.number().int().min(1),
  reverse_proxy: z.boolean(),
});

const TelemetryPlanMixSnapshotSchema = z.strictObject({
  plus: z.string(),
  pro: z.string(),
  team: z.string(),
  free: z.string(),
});

const TelemetryAccountsSnapshotSchema = z.strictObject({
  pool_bucket: z.string(),
  plan_mix: TelemetryPlanMixSnapshotSchema,
  workspace_accounts: z.boolean(),
  routing_policy: z.string(),
  limit_warmup_enabled: z.boolean(),
  egress_proxy_used: z.boolean(),
});

const TelemetryRequestKindsSnapshotSchema = z.strictObject({
  responses: z.number(),
  chat: z.number(),
  images: z.number(),
  unknown: z.number(),
});

const TelemetryTransportMixSnapshotSchema = z.strictObject({
  ws: z.number(),
  http_bridge: z.number(),
});

const TelemetryServiceTierMixSnapshotSchema = z.strictObject({
  default: z.number(),
  flex: z.number(),
  priority: z.number(),
});

const TelemetryModelUsageSnapshotSchema = z.strictObject({
  name: z.string(),
  share: z.number(),
  reasoning: z.record(z.string(), z.number()),
  avg_output_tokens_bucket: z.string(),
});

const TelemetryUsageSnapshotSchema = z.strictObject({
  requests: z.number().int().min(0),
  success_rate: z.number().min(0).max(1),
  tokens_input: z.number().int().min(0),
  tokens_output: z.number().int().min(0),
  tokens_cached_ratio: z.number().min(0).max(1),
  cost_usd_bucket: z.string(),
  request_kinds: TelemetryRequestKindsSnapshotSchema,
  transport_mix: TelemetryTransportMixSnapshotSchema,
  service_tier_mix: TelemetryServiceTierMixSnapshotSchema,
  clients: z.record(z.string(), z.number()),
  clients_other_ratio: z.number().min(0).max(1),
  models: z.array(TelemetryModelUsageSnapshotSchema),
  latency_ms_p50: z.number().int().min(0),
  ttft_ms_p50: z.number().int().min(0),
  ttft_ms_p95: z.number().int().min(0),
  rate_limit_429_ratio: z.number().min(0).max(1),
  top_upstream_errors: z.array(z.string()).max(5),
});

const TelemetryFeaturesSnapshotSchema = z.strictObject({
  api_firewall: z.boolean(),
  quota_planner: z.boolean(),
  sticky_sessions: z.boolean(),
  conversation_archive: z.boolean(),
  automations: z.boolean(),
  fleet: z.boolean(),
  model_sources_count: z.number().int().min(0),
  api_keys_bucket: z.string(),
  prometheus: z.boolean(),
  otel: z.boolean(),
  dashboard_auth: z.boolean(),
  reset_credits: z.boolean(),
  image_api_used: z.boolean(),
});

export const TelemetrySnapshotSchema = z.strictObject({
  schema_version: z.literal(1),
  consent: z.enum(["undecided", "enabled"]),
  instance_id: z.string(),
  version: z.string(),
  python: z.string(),
  os: z.string(),
  arch: z.string(),
  uptime_hours: z.number().int().min(0),
  deploy: TelemetryDeploymentSnapshotSchema,
  accounts: TelemetryAccountsSnapshotSchema,
  usage_7d: TelemetryUsageSnapshotSchema,
  features: TelemetryFeaturesSnapshotSchema,
});

// The exact body the instance would transmit; the consent dialog and the
// settings preview render this envelope verbatim.
export const TelemetrySnapshotEnvelopeSchema = z.strictObject({
  instance_id: z.string(),
  metrics: TelemetrySnapshotSchema,
  timestamp: z.iso.datetime({ offset: true }),
});

export const TelemetryConsentSchema = z.object({
  state: TelemetryConsentStateSchema,
  source: TelemetryConsentSourceSchema,
  active: z.boolean(),
  // Present only when the backend built a snapshot: undecided consent with
  // default source (the dialog case) or an explicit include_preview request.
  preview: TelemetrySnapshotEnvelopeSchema.nullable(),
});

export const TelemetryConsentUpdateRequestSchema = z.object({
  enabled: z.boolean(),
});

export const SubscriptionOverflowPreflightModelSchema = z.object({
  slug: z.string(),
  enabled: z.boolean(),
  neverOverflows: z.boolean(),
  neverOverflowsReason: z.string().nullable().optional().default(null),
  undeclaredToolTypes: z.array(z.string()).default([]),
  supportsVision: z.boolean(),
  supportsStreaming: z.boolean(),
  priced: z.boolean(),
  contextWindowMismatch: z
    .object({
      registry: z.number().int(),
      source: z.number().int().nullable().optional().default(null),
      maxOutputTokens: z.number().int().nullable().optional().default(null),
    })
    .nullable()
    .optional()
    .default(null),
  warnings: z.array(z.string()).default([]),
});

export const SubscriptionOverflowPreflightSchema = z.object({
  sourceId: z.string(),
  sourceName: z.string(),
  sourceEnabled: z.boolean(),
  eligible: z.boolean(),
  blockers: z.array(z.string()).default([]),
  drainUntil: z.iso.datetime({ offset: true }).nullable().optional().default(null),
  servedModels: z.array(SubscriptionOverflowPreflightModelSchema).default([]),
  missingModels: z.array(z.string()).default([]),
  scopedApiKeyCount: z.number().int().min(0),
  livePinCount: z.number().int().min(0),
  tombstoneCount: z.number().int().min(0),
});

export type UpstreamProxyEndpoint = z.infer<typeof UpstreamProxyEndpointSchema>;
export type UpstreamProxyEndpointCreateRequest = z.infer<typeof UpstreamProxyEndpointCreateRequestSchema>;
export type UpstreamProxyEndpointTestResponse = z.infer<typeof UpstreamProxyEndpointTestResponseSchema>;
export type UpstreamProxyPool = z.infer<typeof UpstreamProxyPoolSchema>;
export type UpstreamProxyPoolCreateRequest = z.infer<typeof UpstreamProxyPoolCreateRequestSchema>;
export type UpstreamProxyPoolMemberRequest = z.infer<typeof UpstreamProxyPoolMemberRequestSchema>;
export type AccountProxyBinding = z.infer<typeof AccountProxyBindingSchema>;
export type AccountProxyBindingRequest = z.infer<typeof AccountProxyBindingRequestSchema>;
export type UpstreamProxyAdmin = z.infer<typeof UpstreamProxyAdminSchema>;
export type ModelContextWindowOverride = z.infer<typeof ModelContextWindowOverrideSchema>;
export type ModelContextWindowOverrides = z.infer<typeof ModelContextWindowOverridesSchema>;
export type ModelContextWindowOverrideUpsertRequest = z.infer<typeof ModelContextWindowOverrideUpsertRequestSchema>;
export type TelemetrySnapshot = z.infer<typeof TelemetrySnapshotSchema>;
export type TelemetrySnapshotEnvelope = z.infer<typeof TelemetrySnapshotEnvelopeSchema>;
export type TelemetryConsent = z.infer<typeof TelemetryConsentSchema>;
export type TelemetryConsentUpdateRequest = z.infer<typeof TelemetryConsentUpdateRequestSchema>;
export type SubscriptionOverflowPreflight = z.infer<typeof SubscriptionOverflowPreflightSchema>;
export type SubscriptionOverflowPreflightModel = z.infer<typeof SubscriptionOverflowPreflightModelSchema>;
