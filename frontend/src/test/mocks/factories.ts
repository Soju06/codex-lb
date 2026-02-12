import { z } from "zod";

const isoDateSchema = z.string().datetime({ offset: true });

const accountUsageSchema = z.object({
  primaryRemainingPercent: z.number().nullable(),
  secondaryRemainingPercent: z.number().nullable(),
});

const accountTokenStatusSchema = z.object({
  expiresAt: isoDateSchema.nullable().optional(),
  state: z.string().nullable().optional(),
});

const accountAuthStatusSchema = z.object({
  access: accountTokenStatusSchema.nullable().optional(),
  refresh: accountTokenStatusSchema.nullable().optional(),
  idToken: accountTokenStatusSchema.nullable().optional(),
});

const accountSummarySchema = z.object({
  accountId: z.string(),
  email: z.string(),
  displayName: z.string(),
  planType: z.string(),
  status: z.string(),
  usage: accountUsageSchema.nullable().optional(),
  resetAtPrimary: isoDateSchema.nullable().optional(),
  resetAtSecondary: isoDateSchema.nullable().optional(),
  auth: accountAuthStatusSchema.nullable().optional(),
});

const usageHistoryItemSchema = z.object({
  accountId: z.string(),
  remainingPercentAvg: z.number(),
  capacityCredits: z.number(),
  remainingCredits: z.number(),
});

const usageWindowResponseSchema = z.object({
  windowKey: z.string(),
  windowMinutes: z.number().nullable(),
  accounts: z.array(usageHistoryItemSchema),
});

const dashboardOverviewSchema = z.object({
  lastSyncAt: isoDateSchema.nullable(),
  accounts: z.array(accountSummarySchema),
  summary: z.object({
    primaryWindow: z.object({
      remainingPercent: z.number(),
      capacityCredits: z.number(),
      remainingCredits: z.number(),
      resetAt: isoDateSchema.nullable(),
      windowMinutes: z.number().nullable(),
    }),
    secondaryWindow: z
      .object({
        remainingPercent: z.number(),
        capacityCredits: z.number(),
        remainingCredits: z.number(),
        resetAt: isoDateSchema.nullable(),
        windowMinutes: z.number().nullable(),
      })
      .nullable(),
    cost: z.object({
      currency: z.string(),
      totalUsd7d: z.number(),
    }),
    metrics: z
      .object({
        requests7d: z.number().nullable(),
        tokensSecondaryWindow: z.number().nullable(),
        cachedTokensSecondaryWindow: z.number().nullable(),
        errorRate7d: z.number().nullable(),
        topError: z.string().nullable(),
      })
      .nullable(),
  }),
  windows: z.object({
    primary: usageWindowResponseSchema,
    secondary: usageWindowResponseSchema.nullable(),
  }),
});

const requestLogEntrySchema = z.object({
  requestedAt: isoDateSchema,
  accountId: z.string(),
  requestId: z.string(),
  model: z.string(),
  status: z.enum(["ok", "rate_limit", "quota", "error"]),
  errorCode: z.string().nullable(),
  errorMessage: z.string().nullable(),
  tokens: z.number().nullable(),
  cachedInputTokens: z.number().nullable(),
  reasoningEffort: z.string().nullable(),
  costUsd: z.number().nullable(),
  latencyMs: z.number().nullable(),
});

const requestLogsResponseSchema = z.object({
  requests: z.array(requestLogEntrySchema),
  total: z.number().int().nonnegative(),
  hasMore: z.boolean(),
});

const requestLogFilterOptionsSchema = z.object({
  accountIds: z.array(z.string()),
  modelOptions: z.array(
    z.object({
      model: z.string(),
      reasoningEffort: z.string().nullable(),
    }),
  ),
  statuses: z.array(z.string()),
});

const authSessionSchema = z.object({
  authenticated: z.boolean(),
  passwordRequired: z.boolean(),
  totpRequiredOnLogin: z.boolean(),
  totpConfigured: z.boolean(),
});

const settingsSchema = z.object({
  stickyThreadsEnabled: z.boolean(),
  preferEarlierResetAccounts: z.boolean(),
  totpRequiredOnLogin: z.boolean(),
  totpConfigured: z.boolean(),
  apiKeyAuthEnabled: z.boolean(),
});

const oauthStartSchema = z.object({
  method: z.string(),
  authorizationUrl: z.string().nullable(),
  callbackUrl: z.string().nullable(),
  verificationUrl: z.string().nullable(),
  userCode: z.string().nullable(),
  deviceAuthId: z.string().nullable(),
  intervalSeconds: z.number().nullable(),
  expiresInSeconds: z.number().nullable(),
});

const oauthStatusSchema = z.object({
  status: z.string(),
  errorMessage: z.string().nullable(),
});

const oauthCompleteSchema = z.object({
  status: z.string(),
});

const apiKeySchema = z.object({
  id: z.string(),
  name: z.string(),
  keyPrefix: z.string(),
  allowedModels: z.array(z.string()).nullable(),
  weeklyTokenLimit: z.number().nullable(),
  weeklyTokensUsed: z.number(),
  weeklyResetAt: isoDateSchema,
  expiresAt: isoDateSchema.nullable(),
  isActive: z.boolean(),
  createdAt: isoDateSchema,
  lastUsedAt: isoDateSchema.nullable(),
});

const apiKeyCreateSchema = apiKeySchema.extend({
  key: z.string(),
});

export type AccountSummary = z.infer<typeof accountSummarySchema>;
export type DashboardOverview = z.infer<typeof dashboardOverviewSchema>;
export type RequestLogEntry = z.infer<typeof requestLogEntrySchema>;
export type RequestLogsResponse = z.infer<typeof requestLogsResponseSchema>;
export type RequestLogFilterOptions = z.infer<typeof requestLogFilterOptionsSchema>;
export type DashboardAuthSession = z.infer<typeof authSessionSchema>;
export type DashboardSettings = z.infer<typeof settingsSchema>;
export type OauthStartResponse = z.infer<typeof oauthStartSchema>;
export type OauthStatusResponse = z.infer<typeof oauthStatusSchema>;
export type OauthCompleteResponse = z.infer<typeof oauthCompleteSchema>;
export type ApiKey = z.infer<typeof apiKeySchema>;
export type ApiKeyCreateResponse = z.infer<typeof apiKeyCreateSchema>;

const BASE_TIME = new Date("2026-01-01T12:00:00Z");

function offsetIso(minutes: number): string {
  return new Date(BASE_TIME.getTime() + minutes * 60_000).toISOString();
}

export function createAccountSummary(overrides: Partial<AccountSummary> = {}): AccountSummary {
  return accountSummarySchema.parse({
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
    ...overrides,
  };
  return dashboardOverviewSchema.parse(response);
}

export function createRequestLogEntry(overrides: Partial<RequestLogEntry> = {}): RequestLogEntry {
  return requestLogEntrySchema.parse({
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
  return requestLogsResponseSchema.parse({
    requests,
    total,
    hasMore,
  });
}

export function createRequestLogFilterOptions(
  overrides: Partial<RequestLogFilterOptions> = {},
): RequestLogFilterOptions {
  return requestLogFilterOptionsSchema.parse({
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
  return authSessionSchema.parse({
    authenticated: true,
    passwordRequired: true,
    totpRequiredOnLogin: false,
    totpConfigured: true,
    ...overrides,
  });
}

export function createDashboardSettings(overrides: Partial<DashboardSettings> = {}): DashboardSettings {
  return settingsSchema.parse({
    stickyThreadsEnabled: true,
    preferEarlierResetAccounts: false,
    totpRequiredOnLogin: false,
    totpConfigured: true,
    apiKeyAuthEnabled: true,
    ...overrides,
  });
}

export function createOauthStartResponse(
  overrides: Partial<OauthStartResponse> = {},
): OauthStartResponse {
  return oauthStartSchema.parse({
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
  return oauthStatusSchema.parse({
    status: "pending",
    errorMessage: null,
    ...overrides,
  });
}

export function createOauthCompleteResponse(
  overrides: Partial<OauthCompleteResponse> = {},
): OauthCompleteResponse {
  return oauthCompleteSchema.parse({
    status: "ok",
    ...overrides,
  });
}

export function createApiKey(overrides: Partial<ApiKey> = {}): ApiKey {
  return apiKeySchema.parse({
    id: "key_1",
    name: "Default key",
    keyPrefix: "sk-test",
    allowedModels: ["gpt-5.1"],
    weeklyTokenLimit: 1_000_000,
    weeklyTokensUsed: 125_000,
    weeklyResetAt: offsetIso(7 * 24 * 60),
    expiresAt: offsetIso(30 * 24 * 60),
    isActive: true,
    createdAt: offsetIso(-60),
    lastUsedAt: offsetIso(-5),
    ...overrides,
  });
}

export function createApiKeyCreateResponse(
  overrides: Partial<ApiKeyCreateResponse> = {},
): ApiKeyCreateResponse {
  return apiKeyCreateSchema.parse({
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
      weeklyTokenLimit: null,
      weeklyTokensUsed: 12_000,
      isActive: false,
      expiresAt: null,
      lastUsedAt: null,
    }),
  ];
}
