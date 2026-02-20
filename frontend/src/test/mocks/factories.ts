import {
  AccountSummarySchema,
  AccountTrendsResponseSchema,
  OauthStartResponseSchema,
  OauthStatusResponseSchema,
  OauthCompleteResponseSchema,
} from "@/features/accounts/schemas";
import type {
  AccountSummary,
  AccountTrendsResponse,
  OauthStartResponse,
  OauthStatusResponse,
} from "@/features/accounts/schemas";
import {
  DashboardOverviewSchema,
  RequestLogSchema,
  RequestLogsResponseSchema,
  RequestLogFilterOptionsSchema,
} from "@/features/dashboard/schemas";
import type {
  DashboardOverview,
  RequestLog,
  RequestLogsResponse,
  RequestLogFilterOptions,
} from "@/features/dashboard/schemas";
import { AuthSessionSchema } from "@/features/auth/schemas";
import type { AuthSession } from "@/features/auth/schemas";
import { DashboardSettingsSchema } from "@/features/settings/schemas";
import type { DashboardSettings } from "@/features/settings/schemas";
import {
  ApiKeySchema,
  ApiKeyCreateResponseSchema,
} from "@/features/api-keys/schemas";
import type { ApiKey, ApiKeyCreateResponse } from "@/features/api-keys/schemas";
import { z } from "zod";

// Backward-compatible type aliases
export type RequestLogEntry = RequestLog;
export type DashboardAuthSession = AuthSession;
export type OauthCompleteResponse = z.infer<typeof OauthCompleteResponseSchema>;

export type {
  AccountSummary,
  AccountTrendsResponse,
  DashboardOverview,
  RequestLogsResponse,
  RequestLogFilterOptions,
  DashboardSettings,
  OauthStartResponse,
  OauthStatusResponse,
  ApiKey,
  ApiKeyCreateResponse,
};

const BASE_TIME = new Date("2026-01-01T12:00:00Z");

function offsetIso(minutes: number): string {
  return new Date(BASE_TIME.getTime() + minutes * 60_000).toISOString();
}

export function createAccountSummary(overrides: Partial<AccountSummary> = {}): AccountSummary {
  return AccountSummarySchema.parse({
    accountId: "acc_primary",
    email: "primary@example.com",
    displayName: "primary@example.com",
    planType: "plus",
    status: "active",
    usage: {
      primaryRemainingPercent: 82,
      secondaryRemainingPercent: 67,
    },
    resetAtPrimary: offsetIso(60),
    resetAtSecondary: offsetIso(24 * 60),
    windowMinutesPrimary: 300,
    windowMinutesSecondary: 10_080,
    auth: {
      access: { expiresAt: offsetIso(30), state: null },
      refresh: { state: "stored" },
      idToken: { state: "parsed" },
    },
    ...overrides,
  });
}

export function createDefaultAccounts(): AccountSummary[] {
  return [
    createAccountSummary(),
    createAccountSummary({
      accountId: "acc_secondary",
      email: "secondary@example.com",
      displayName: "secondary@example.com",
      status: "paused",
      usage: {
        primaryRemainingPercent: 45,
        secondaryRemainingPercent: 12,
      },
    }),
  ];
}

function createTrendPoints(baseValue: number, count = 28): Array<{ t: string; v: number }> {
  return Array.from({ length: count }, (_, i) => ({
    t: new Date(BASE_TIME.getTime() - (count - i) * 6 * 3600_000).toISOString(),
    v: Math.max(0, baseValue + Math.sin(i) * baseValue * 0.3),
  }));
}

export function createDashboardOverview(overrides: Partial<DashboardOverview> = {}): DashboardOverview {
  const accounts = overrides.accounts ?? createDefaultAccounts();
  const response = {
    lastSyncAt: offsetIso(-5),
    accounts,
    summary: {
      primaryWindow: {
        remainingPercent: 63.5,
        capacityCredits: 225,
        remainingCredits: 142.875,
        resetAt: offsetIso(60),
        windowMinutes: 300,
      },
      secondaryWindow: {
        remainingPercent: 55.2,
        capacityCredits: 7560,
        remainingCredits: 4173.12,
        resetAt: offsetIso(24 * 60),
        windowMinutes: 10_080,
      },
      cost: {
        currency: "USD",
        totalUsd7d: 1.82,
      },
      metrics: {
        requests7d: 228,
        tokensSecondaryWindow: 45_000,
        cachedTokensSecondaryWindow: 8_200,
        errorRate7d: 0.028,
        topError: "rate_limit_exceeded",
      },
    },
    windows: {
      primary: {
        windowKey: "primary",
        windowMinutes: 300,
        accounts: accounts.map((account) => ({
          accountId: account.accountId,
          remainingPercentAvg: account.usage?.primaryRemainingPercent ?? 0,
          capacityCredits: 225,
          remainingCredits: ((account.usage?.primaryRemainingPercent ?? 0) / 100) * 225,
        })),
      },
      secondary: {
        windowKey: "secondary",
        windowMinutes: 10_080,
        accounts: accounts.map((account) => ({
          accountId: account.accountId,
          remainingPercentAvg: account.usage?.secondaryRemainingPercent ?? 0,
          capacityCredits: 7560,
          remainingCredits: ((account.usage?.secondaryRemainingPercent ?? 0) / 100) * 7560,
        })),
      },
    },
    trends: {
      requests: createTrendPoints(8),
      tokens: createTrendPoints(1600),
      cost: createTrendPoints(0.065),
      errorRate: createTrendPoints(0.03),
    },
    ...overrides,
  };
  return DashboardOverviewSchema.parse(response);
}

export function createRequestLogEntry(overrides: Partial<RequestLogEntry> = {}): RequestLogEntry {
  return RequestLogSchema.parse({
    requestedAt: offsetIso(-1),
    accountId: "acc_primary",
    requestId: "req_1",
    model: "gpt-5.1",
    status: "ok",
    errorCode: null,
    errorMessage: null,
    tokens: 1800,
    cachedInputTokens: 320,
    reasoningEffort: null,
    costUsd: 0.0132,
    latencyMs: 920,
    ...overrides,
  });
}

export function createDefaultRequestLogs(): RequestLogEntry[] {
  return [
    createRequestLogEntry(),
    createRequestLogEntry({
      requestId: "req_2",
      accountId: "acc_secondary",
      status: "rate_limit",
      errorCode: "rate_limit_exceeded",
      errorMessage: "Rate limit reached",
      tokens: 0,
      cachedInputTokens: null,
      costUsd: 0,
      requestedAt: offsetIso(-2),
    }),
    createRequestLogEntry({
      requestId: "req_3",
      status: "quota",
      errorCode: "insufficient_quota",
      errorMessage: "Quota exceeded",
      tokens: 0,
      cachedInputTokens: null,
      costUsd: 0,
      requestedAt: offsetIso(-3),
    }),
  ];
}

export function createRequestLogsResponse(
  requests: RequestLogEntry[],
  total: number,
  hasMore: boolean,
): RequestLogsResponse {
  return RequestLogsResponseSchema.parse({
    requests,
    total,
    hasMore,
  });
}

export function createRequestLogFilterOptions(
  overrides: Partial<RequestLogFilterOptions> = {},
): RequestLogFilterOptions {
  return RequestLogFilterOptionsSchema.parse({
    accountIds: ["acc_primary", "acc_secondary"],
    modelOptions: [
      { model: "gpt-5.1", reasoningEffort: null },
      { model: "gpt-5.1", reasoningEffort: "high" },
    ],
    statuses: ["ok", "rate_limit", "quota"],
    ...overrides,
  });
}

export function createDashboardAuthSession(
  overrides: Partial<DashboardAuthSession> = {},
): DashboardAuthSession {
  return AuthSessionSchema.parse({
    authenticated: true,
    passwordRequired: true,
    totpRequiredOnLogin: false,
    totpConfigured: true,
    ...overrides,
  });
}

export function createDashboardSettings(overrides: Partial<DashboardSettings> = {}): DashboardSettings {
  return DashboardSettingsSchema.parse({
    stickyThreadsEnabled: true,
    preferEarlierResetAccounts: false,
    importWithoutOverwrite: false,
    totpRequiredOnLogin: false,
    totpConfigured: true,
    apiKeyAuthEnabled: true,
    ...overrides,
  });
}

export function createOauthStartResponse(
  overrides: Partial<OauthStartResponse> = {},
): OauthStartResponse {
  return OauthStartResponseSchema.parse({
    method: "browser",
    authorizationUrl: "https://auth.example.com/start",
    callbackUrl: "http://localhost:3000/api/oauth/callback",
    verificationUrl: null,
    userCode: null,
    deviceAuthId: null,
    intervalSeconds: null,
    expiresInSeconds: null,
    ...overrides,
  });
}

export function createOauthStatusResponse(
  overrides: Partial<OauthStatusResponse> = {},
): OauthStatusResponse {
  return OauthStatusResponseSchema.parse({
    status: "pending",
    errorMessage: null,
    ...overrides,
  });
}

export function createOauthCompleteResponse(
  overrides: Partial<OauthCompleteResponse> = {},
): OauthCompleteResponse {
  return OauthCompleteResponseSchema.parse({
    status: "ok",
    ...overrides,
  });
}

export function createApiKey(overrides: Partial<ApiKey> = {}): ApiKey {
  return ApiKeySchema.parse({
    id: "key_1",
    name: "Default key",
    keyPrefix: "sk-test",
    allowedModels: ["gpt-5.1"],
    expiresAt: offsetIso(30 * 24 * 60),
    isActive: true,
    createdAt: offsetIso(-60),
    lastUsedAt: offsetIso(-5),
    limits: [
      {
        id: 1,
        limitType: "total_tokens",
        limitWindow: "weekly",
        maxValue: 1_000_000,
        currentValue: 125_000,
        modelFilter: null,
        resetAt: offsetIso(7 * 24 * 60),
      },
    ],
    ...overrides,
  });
}

export function createApiKeyCreateResponse(
  overrides: Partial<ApiKeyCreateResponse> = {},
): ApiKeyCreateResponse {
  return ApiKeyCreateResponseSchema.parse({
    ...createApiKey(),
    key: "sk-test-generated-secret",
    ...overrides,
  });
}

export function createDefaultApiKeys(): ApiKey[] {
  return [
    createApiKey(),
    createApiKey({
      id: "key_2",
      name: "Read only key",
      keyPrefix: "sk-second",
      allowedModels: ["gpt-4o-mini"],
      isActive: false,
      expiresAt: null,
      lastUsedAt: null,
      limits: [],
    }),
  ];
}

function createUsageTrendPoints(
  basePercent: number,
  count = 28,
): Array<{ t: string; v: number }> {
  return Array.from({ length: count }, (_, i) => ({
    t: new Date(BASE_TIME.getTime() - (count - i) * 6 * 3600_000).toISOString(),
    v: Math.max(0, Math.min(100, basePercent + Math.sin(i) * 15)),
  }));
}

export function createAccountTrends(
  accountId: string,
  overrides: Partial<AccountTrendsResponse> = {},
): AccountTrendsResponse {
  return AccountTrendsResponseSchema.parse({
    accountId,
    primary: createUsageTrendPoints(80),
    secondary: createUsageTrendPoints(55),
    ...overrides,
  });
}
