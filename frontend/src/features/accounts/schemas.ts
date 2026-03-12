import { z } from "zod";

export const UsageTrendPointSchema = z.object({
  t: z.string().datetime({ offset: true }),
  v: z.number(),
});

export const AccountUsageTrendSchema = z.object({
  primary: z.array(UsageTrendPointSchema),
  secondary: z.array(UsageTrendPointSchema),
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

export const AccountSummarySchema = z.object({
  accountId: z.string(),
  email: z.string(),
  displayName: z.string(),
  planType: z.string(),
  status: z.string(),
  usage: AccountUsageSchema.nullable().optional(),
  resetAtPrimary: z.string().datetime({ offset: true }).nullable().optional(),
  resetAtSecondary: z.string().datetime({ offset: true }).nullable().optional(),
  windowMinutesPrimary: z.number().nullable().optional(),
  windowMinutesSecondary: z.number().nullable().optional(),
  requestUsage: AccountRequestUsageSchema.nullable().optional(),
  auth: AccountAuthSchema.nullable().optional(),
  additionalQuotas: z.array(AccountAdditionalQuotaSchema).default([]),
});

export const AccountTrendsResponseSchema = z.object({
  accountId: z.string(),
  primary: z.array(UsageTrendPointSchema),
  secondary: z.array(UsageTrendPointSchema),
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

export const AccountActionResponseSchema = z.object({
  status: z.string(),
});

export const OauthStartRequestSchema = z.object({
  forceMethod: z.string().optional(),
});

export const OauthStartResponseSchema = z.object({
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
  deviceAuthId: z.string().optional(),
  userCode: z.string().optional(),
});

export const OauthCompleteResponseSchema = z.object({
  status: z.string(),
});

export const ManualOauthCallbackRequestSchema = z.object({
  callbackUrl: z.string(),
});

export const ManualOauthCallbackResponseSchema = z.object({
  status: z.string(),
  errorMessage: z.string().nullable(),
});

export const RuntimeConnectAddressResponseSchema = z.object({
  connectAddress: z.string(),
});

export const OAuthStateSchema = z.object({
  status: z.enum(["idle", "starting", "pending", "success", "error"]),
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
export type AccountAdditionalWindow = z.infer<typeof AccountAdditionalWindowSchema>;
export type AccountAdditionalQuota = z.infer<typeof AccountAdditionalQuotaSchema>;
export type AccountTrendsResponse = z.infer<typeof AccountTrendsResponseSchema>;
export type OauthStartResponse = z.infer<typeof OauthStartResponseSchema>;
export type OauthStatusResponse = z.infer<typeof OauthStatusResponseSchema>;
export type ManualOauthCallbackResponse = z.infer<typeof ManualOauthCallbackResponseSchema>;
export type RuntimeConnectAddressResponse = z.infer<
  typeof RuntimeConnectAddressResponseSchema
>;
export type OAuthState = z.infer<typeof OAuthStateSchema>;
export type ImportState = z.infer<typeof ImportStateSchema>;
