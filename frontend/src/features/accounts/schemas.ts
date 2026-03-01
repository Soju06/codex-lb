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

export const AccountTokenStatusSchema = z.object({
  expiresAt: z.string().datetime({ offset: true }).nullable().optional(),
  state: z.string().nullable().optional(),
});

export const AccountAuthSchema = z.object({
  access: AccountTokenStatusSchema.nullable().optional(),
  refresh: AccountTokenStatusSchema.nullable().optional(),
  idToken: AccountTokenStatusSchema.nullable().optional(),
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
  auth: AccountAuthSchema.nullable().optional(),
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
  callbackUrl: z.string().optional(),
});

export const OauthCompleteResponseSchema = z.object({
  status: z.string(),
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
export type AccountTrendsResponse = z.infer<typeof AccountTrendsResponseSchema>;
export type OauthStartResponse = z.infer<typeof OauthStartResponseSchema>;
export type OauthStatusResponse = z.infer<typeof OauthStatusResponseSchema>;
export type OAuthState = z.infer<typeof OAuthStateSchema>;
export type ImportState = z.infer<typeof ImportStateSchema>;
