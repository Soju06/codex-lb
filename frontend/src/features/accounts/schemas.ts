import { z } from "zod";

export const UsageTrendPointSchema = z.object({
  t: z.string().datetime({ offset: true }),
  v: z.number(),
});

export const AccountUsageTrendSchema = z.object({
  primary: z.array(UsageTrendPointSchema),
  secondary: z.array(UsageTrendPointSchema),
  secondaryScheduled: z.array(UsageTrendPointSchema).default([]),
});

export const AccountUsageSchema = z.object({
  primaryRemainingPercent: z.number().nullable(),
  secondaryRemainingPercent: z.number().nullable(),
});

export const AccountRequestUsageSchema = z.object({
  requestCount: z.number().int().nonnegative(),
  totalTokens: z.number().int().nonnegative(),
  cachedInputTokens: z.number().int().nonnegative(),
  totalCostUsd: z.number().nonnegative(),
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

export const AccountLimitWarmupStatusSchema = z.object({
  window: z.enum(["primary", "secondary"]).or(z.string()),
  resetAt: z.number().int(),
  status: z.string(),
  model: z.string(),
  attemptedAt: z.string().datetime({ offset: true }),
  completedAt: z.string().datetime({ offset: true }).nullable().optional(),
  errorCode: z.string().nullable().optional(),
  errorMessage: z.string().nullable().optional(),
});

export const AccountAdditionalWindowSchema = z.object({
  usedPercent: z.number(),
  resetAt: z.number().nullable().optional(),
  windowMinutes: z.number().nullable().optional(),
});

export const AccountAdditionalQuotaSchema = z.object({
  quotaKey: z.string().nullable().optional(),
  limitName: z.string(),
  meteredFeature: z.string(),
  displayLabel: z.string().nullable().optional(),
  primaryWindow: AccountAdditionalWindowSchema.nullable().optional(),
  secondaryWindow: AccountAdditionalWindowSchema.nullable().optional(),
});

export const AccountProxyInputSchema = z
  .object({
    host: z
      .string()
      .trim()
      .min(1, "Host is required")
      .max(253, "Host is too long"),
    port: z
      .number({ error: "Port must be a number" })
      .int("Port must be a whole number")
      .min(1, "Port must be 1-65535")
      .max(65535, "Port must be 1-65535"),
    username: z.string().max(255).optional().nullable(),
    password: z
      .string()
      .max(1024)
      .optional()
      .nullable()
      .describe("Write-only. Omit on edit to keep the existing password."),
    clearPassword: z.boolean().default(false),
    remoteDns: z.boolean().default(true),
    label: z.string().max(128).optional().nullable(),
  })
  .strict();

export const AccountProxySummarySchema = z.object({
  host: z.string(),
  port: z.number().int(),
  username: z.string().nullable().optional(),
  hasPassword: z.boolean().default(false),
  remoteDns: z.boolean().default(true),
  label: z.string().nullable().optional(),
  lastValidatedAt: z.string().datetime({ offset: true }).nullable().optional(),
});

export const AccountProxyClearResponseSchema = z.object({
  status: z.string(),
});

export const AccountSummarySchema = z.object({
  accountId: z.string(),
  email: z.string(),
  alias: z.string().nullable().optional(),
  displayName: z.string(),
  planType: z.string(),
  status: z.string(),
  usage: AccountUsageSchema.nullable().optional(),
  resetAtPrimary: z.string().datetime({ offset: true }).nullable().optional(),
  resetAtSecondary: z.string().datetime({ offset: true }).nullable().optional(),
  windowMinutesPrimary: z.number().nullable().optional(),
  windowMinutesSecondary: z.number().nullable().optional(),
  capacityCreditsSecondary: z.number().nullable().optional(),
  remainingCreditsSecondary: z.number().nullable().optional(),
  requestUsage: AccountRequestUsageSchema.nullable().optional(),
  auth: AccountAuthSchema.nullable().optional(),
  additionalQuotas: z.array(AccountAdditionalQuotaSchema).default([]),
  limitWarmupEnabled: z.boolean().default(false),
  limitWarmup: AccountLimitWarmupStatusSchema.nullable().optional(),
  proxy: AccountProxySummarySchema.nullable().optional(),
});

export const AccountTrendsResponseSchema = z.object({
  accountId: z.string(),
  primary: z.array(UsageTrendPointSchema),
  secondary: z.array(UsageTrendPointSchema),
  secondaryScheduled: z.array(UsageTrendPointSchema).default([]),
});

export const AccountsResponseSchema = z.object({
  accounts: z.array(AccountSummarySchema),
});

export const AccountImportResponseSchema = z.object({
  accountId: z.string(),
  email: z.string(),
  planType: z.string(),
  status: z.string(),
});

export const OpenCodeOAuthAuthSchema = z.object({
  type: z.literal("oauth"),
  refresh: z.string(),
  access: z.string(),
  expires: z.number().int().nonnegative(),
  accountId: z.string().nullable().optional(),
});

export const OpenCodeAuthJsonSchema = z.object({
  openai: OpenCodeOAuthAuthSchema,
});

export const AccountOpenCodeAuthExportAccountSchema = z.object({
  accountId: z.string(),
  chatgptAccountId: z.string().nullable().optional(),
  email: z.string(),
});

export const AccountOpenCodeAuthExportResponseSchema = z.object({
  filename: z.string(),
  account: AccountOpenCodeAuthExportAccountSchema,
  authJson: OpenCodeAuthJsonSchema,
});

export const AccountActionResponseSchema = z.object({
  status: z.string(),
});

export const AccountAliasRequestSchema = z.object({
  alias: z.string().max(255).nullable(),
});

export const AccountAliasResponseSchema = z.object({
  accountId: z.string(),
  alias: z.string().nullable(),
});

export const AccountLimitWarmupUpdateRequestSchema = z.object({
  enabled: z.boolean(),
});

export const AccountLimitWarmupUpdateResponseSchema = z.object({
  status: z.string(),
  enabled: z.boolean(),
});

export const AccountExportResponseSchema = z.object({
  accountId: z.string(),
  email: z.string(),
  planType: z.string(),
  status: z.string(),
  authJson: z.string(),
});

export const OauthStartRequestSchema = z.object({
  forceMethod: z.string().optional(),
  // When true, the OAuth attempt's three token-arrival paths
  // (auto callback / manual callback / device polling) stash the
  // acquired tokens in transient state instead of persisting an
  // Account. The dashboard's "Finish setup" step then calls
  // /api/oauth/complete with the operator-supplied proxy fields,
  // which atomically probes and persists.
  expectProxy: z.boolean().optional(),
  proxyHost: z.string().max(253).optional(),
  proxyPort: z.number().int().min(1).max(65535).optional(),
  proxyUsername: z.string().max(255).optional(),
  proxyPassword: z.string().max(1024).optional(),
  proxyRemoteDns: z.boolean().optional(),
  proxyLabel: z.string().max(128).optional(),
});

export const OauthStartResponseSchema = z.object({
  flowId: z.string().nullable().optional(),
  method: z.string(),
  authorizationUrl: z.string().nullable(),
  callbackUrl: z.string().nullable(),
  verificationUrl: z.string().nullable(),
  userCode: z.string().nullable(),
  deviceAuthId: z.string().nullable(),
  intervalSeconds: z.number().nullable(),
  expiresInSeconds: z.number().nullable(),
});

export const OauthStatusResponseSchema = z.object({
  status: z.string(),
  errorMessage: z.string().nullable(),
});

export const OauthCompleteRequestSchema = z.object({
  flowId: z.string().optional(),
  deviceAuthId: z.string().optional(),
  userCode: z.string().optional(),
  // Optional proxy fields mirroring AccountProxyInputSchema for the
  // OAuth-with-proxy atomic-persistence path. Field shape is flat
  // (not nested) to keep the wire contract symmetric with the import
  // multipart form.
  proxyHost: z.string().max(253).optional(),
  proxyPort: z.number().int().min(1).max(65535).optional(),
  proxyUsername: z.string().max(255).optional(),
  proxyPassword: z.string().max(1024).optional(),
  proxyRemoteDns: z.boolean().optional(),
  proxyLabel: z.string().max(128).optional(),
});

export const OauthCompleteResponseSchema = z.object({
  status: z.string(),
  accountId: z.string().nullable().optional(),
  proxy: AccountProxySummarySchema.nullable().optional(),
});

export const OauthResetResponseSchema = z.object({
  status: z.string(),
});

export const ManualOauthCallbackRequestSchema = z.object({
  callbackUrl: z.string(),
  flowId: z.string().optional(),
});

export const ManualOauthCallbackResponseSchema = z.object({
  status: z.enum(["success", "tokens_ready", "error"]),
  errorMessage: z.string().nullable(),
});

export const RuntimeConnectAddressResponseSchema = z.object({
  connectAddress: z.string(),
});

export const OAuthStateSchema = z.object({
  flowId: z.string().nullable().optional(),
  // "tokens_ready" surfaces the deferred-persistence state for the
  // expect_proxy=true path: the backend has acquired tokens but is
  // holding them in transient state until the dashboard calls
  // /api/oauth/complete with proxy fields.
  status: z.enum([
    "idle",
    "starting",
    "pending",
    "tokens_ready",
    "success",
    "error",
  ]),
  method: z.enum(["browser", "device"]).nullable(),
  authorizationUrl: z.string().nullable(),
  callbackUrl: z.string().nullable(),
  verificationUrl: z.string().nullable(),
  userCode: z.string().nullable(),
  deviceAuthId: z.string().nullable(),
  intervalSeconds: z.number().nullable(),
  expiresInSeconds: z.number().nullable(),
  errorMessage: z.string().nullable(),
});

export const ImportStateSchema = z.object({
  status: z.enum(["idle", "uploading", "success", "error"]),
  message: z.string().nullable(),
});

export type UsageTrendPoint = z.infer<typeof UsageTrendPointSchema>;
export type AccountUsageTrend = z.infer<typeof AccountUsageTrendSchema>;
export type AccountSummary = z.infer<typeof AccountSummarySchema>;
export type AccountAliasResponse = z.infer<typeof AccountAliasResponseSchema>;
export type AccountLimitWarmupStatus = z.infer<typeof AccountLimitWarmupStatusSchema>;
export type AccountProxyInput = z.infer<typeof AccountProxyInputSchema>;
export type AccountProxySummary = z.infer<typeof AccountProxySummarySchema>;
export type AccountImportResponse = z.infer<typeof AccountImportResponseSchema>;
export type AccountExportResponse = z.infer<typeof AccountExportResponseSchema>;
export type AccountAdditionalWindow = z.infer<typeof AccountAdditionalWindowSchema>;
export type AccountAdditionalQuota = z.infer<typeof AccountAdditionalQuotaSchema>;
export type AccountTrendsResponse = z.infer<typeof AccountTrendsResponseSchema>;
export type OpenCodeAuthJson = z.infer<typeof OpenCodeAuthJsonSchema>;
export type AccountOpenCodeAuthExportResponse = z.infer<
  typeof AccountOpenCodeAuthExportResponseSchema
>;
export type OauthStartResponse = z.infer<typeof OauthStartResponseSchema>;
export type OauthStatusResponse = z.infer<typeof OauthStatusResponseSchema>;
export type ManualOauthCallbackResponse = z.infer<typeof ManualOauthCallbackResponseSchema>;
export type RuntimeConnectAddressResponse = z.infer<
  typeof RuntimeConnectAddressResponseSchema
>;
export type OAuthState = z.infer<typeof OAuthStateSchema>;
export type ImportState = z.infer<typeof ImportStateSchema>;
