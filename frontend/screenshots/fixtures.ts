import {
  createAccountSummary,
  createApiKey,
  createDashboardAuthSession,
  createDashboardOverview,
  createDashboardSettings,
  createRequestLogEntry,
  createRequestLogFilterOptions,
  createRequestLogsResponse,
  type AccountSummary,
  type RequestLogEntry,
} from "../src/test/mocks/factories";

// ── Time helpers ──

const BASE_TIME = new Date("2026-01-31T18:00:00Z");

function offsetIso(minutes: number): string {
  return new Date(BASE_TIME.getTime() + minutes * 60_000).toISOString();
}

function trendIso(hoursAgo: number): string {
  return new Date(BASE_TIME.getTime() - hoursAgo * 3600_000).toISOString();
}

// ── 7 accounts (6 plus + 1 pro) — based on real W04 proportions ──

const PLUS_CAP_PRIMARY = 225;
const PRO_CAP_PRIMARY = 1500;
const PLUS_CAP_SECONDARY = 7560;
const PRO_CAP_SECONDARY = 50400;

export const accounts: AccountSummary[] = [
  // Pro account — heaviest user (~65% of traffic)
  createAccountSummary({
    accountId: "acc_01",
    email: "alex.research@fastlab.io",
    displayName: "alex.research@fastlab.io",
    planType: "pro",
    status: "active",
    usage: { primaryRemainingPercent: 100, secondaryRemainingPercent: 34 },
    resetAtPrimary: offsetIso(48),
    resetAtSecondary: offsetIso(3 * 24 * 60),
  }),
  // Plus accounts — distributed remaining traffic
  createAccountSummary({
    accountId: "acc_02",
    email: "dev.james@gmail.com",
    displayName: "dev.james@gmail.com",
    planType: "plus",
    status: "active",
    usage: { primaryRemainingPercent: 100, secondaryRemainingPercent: 3 },
    resetAtPrimary: offsetIso(52),
    resetAtSecondary: offsetIso(3 * 24 * 60),
  }),
  createAccountSummary({
    accountId: "acc_03",
    email: "sarah.kim@gmail.com",
    displayName: "sarah.kim@gmail.com",
    planType: "plus",
    status: "active",
    usage: { primaryRemainingPercent: 100, secondaryRemainingPercent: 5 },
    resetAtPrimary: offsetIso(55),
    resetAtSecondary: offsetIso(3 * 24 * 60),
  }),
  createAccountSummary({
    accountId: "acc_04",
    email: "admin@startupco.io",
    displayName: "admin@startupco.io",
    planType: "plus",
    status: "active",
    usage: { primaryRemainingPercent: 100, secondaryRemainingPercent: 3 },
    resetAtPrimary: offsetIso(35),
    resetAtSecondary: offsetIso(3 * 24 * 60),
  }),
  createAccountSummary({
    accountId: "acc_05",
    email: "mike.ops@gmail.com",
    displayName: "mike.ops@gmail.com",
    planType: "plus",
    status: "active",
    usage: { primaryRemainingPercent: 100, secondaryRemainingPercent: 4 },
    resetAtPrimary: offsetIso(42),
    resetAtSecondary: offsetIso(3 * 24 * 60),
  }),
  createAccountSummary({
    accountId: "acc_06",
    email: "emma.build@gmail.com",
    displayName: "emma.build@gmail.com",
    planType: "plus",
    status: "active",
    usage: { primaryRemainingPercent: 100, secondaryRemainingPercent: 0 },
    resetAtPrimary: offsetIso(60),
    resetAtSecondary: offsetIso(3 * 24 * 60),
  }),
  createAccountSummary({
    accountId: "acc_07",
    email: "ci.bot@devmail.net",
    displayName: "ci.bot@devmail.net",
    planType: "plus",
    status: "active",
    usage: { primaryRemainingPercent: 100, secondaryRemainingPercent: 0 },
    resetAtPrimary: offsetIso(45),
    resetAtSecondary: offsetIso(3 * 24 * 60),
  }),
];

// ── Request logs — realistic heavy Codex sessions ──

export const requestLogs: RequestLogEntry[] = [
  createRequestLogEntry({
    requestId: "req_01",
    accountId: "acc_01",
    model: "gpt-5.2-codex",
    reasoningEffort: "high",
    status: "ok",
    tokens: 44_820,
    cachedInputTokens: 42_240,
    costUsd: 1.68,
    latencyMs: 14_200,
    requestedAt: offsetIso(-2),
  }),
  createRequestLogEntry({
    requestId: "req_02",
    accountId: "acc_01",
    model: "gpt-5.2-codex",
    reasoningEffort: "high",
    status: "ok",
    tokens: 38_450,
    cachedInputTokens: 36_480,
    costUsd: 1.42,
    latencyMs: 9_800,
    requestedAt: offsetIso(-5),
  }),
  createRequestLogEntry({
    requestId: "req_03",
    accountId: "acc_02",
    model: "gpt-5.2-codex",
    reasoningEffort: "high",
    status: "ok",
    tokens: 41_200,
    cachedInputTokens: 39_680,
    costUsd: 1.55,
    latencyMs: 11_400,
    requestedAt: offsetIso(-8),
  }),
  createRequestLogEntry({
    requestId: "req_04",
    accountId: "acc_01",
    model: "gpt-5.2",
    reasoningEffort: "high",
    status: "ok",
    tokens: 128_500,
    cachedInputTokens: 121_600,
    costUsd: 4.82,
    latencyMs: 18_500,
    requestedAt: offsetIso(-12),
  }),
  createRequestLogEntry({
    requestId: "req_05",
    accountId: "acc_03",
    model: "gpt-5.2-codex",
    reasoningEffort: "medium",
    status: "ok",
    tokens: 35_600,
    cachedInputTokens: 33_280,
    costUsd: 0.92,
    latencyMs: 7_200,
    requestedAt: offsetIso(-18),
  }),
  createRequestLogEntry({
    requestId: "req_06",
    accountId: "acc_04",
    model: "gpt-5.2-codex",
    reasoningEffort: "high",
    status: "ok",
    tokens: 29_400,
    cachedInputTokens: 27_520,
    costUsd: 1.08,
    latencyMs: 12_600,
    requestedAt: offsetIso(-25),
  }),
  createRequestLogEntry({
    requestId: "req_07",
    accountId: "acc_05",
    model: "gpt-5.1-codex-mini",
    reasoningEffort: "medium",
    status: "ok",
    tokens: 12_800,
    cachedInputTokens: 4_480,
    costUsd: 0.18,
    latencyMs: 2_850,
    requestedAt: offsetIso(-32),
  }),
  createRequestLogEntry({
    requestId: "req_08",
    accountId: "acc_01",
    model: "gpt-5.2-codex",
    reasoningEffort: "high",
    status: "error",
    errorCode: "upstream_error",
    errorMessage: "Upstream service temporarily unavailable",
    tokens: 0,
    cachedInputTokens: null,
    costUsd: 0,
    latencyMs: 38,
    requestedAt: offsetIso(-40),
  }),
  createRequestLogEntry({
    requestId: "req_09",
    accountId: "acc_02",
    model: "gpt-5.2-codex",
    reasoningEffort: "high",
    status: "error",
    errorCode: "upstream_unavailable",
    errorMessage: "Service unavailable — retry later",
    tokens: 0,
    cachedInputTokens: null,
    costUsd: 0,
    latencyMs: 52,
    requestedAt: offsetIso(-48),
  }),
  createRequestLogEntry({
    requestId: "req_10",
    accountId: "acc_01",
    model: "gpt-5.2-codex",
    reasoningEffort: "high",
    status: "ok",
    tokens: 52_300,
    cachedInputTokens: 48_640,
    costUsd: 1.95,
    latencyMs: 22_800,
    requestedAt: offsetIso(-55),
  }),
];

// ── 3 API keys (heavy limits) ──

export const apiKeys = [
  createApiKey({
    id: "key_prod",
    name: "Production",
    keyPrefix: "sk-prod",
    allowedModels: null,
    isActive: true,
    expiresAt: offsetIso(90 * 24 * 60),
    lastUsedAt: offsetIso(-1),
    limits: [
      {
        id: 1,
        limitType: "total_tokens",
        limitWindow: "weekly",
        maxValue: 3_000_000_000,
        currentValue: 1_918_000_000,
        modelFilter: null,
        resetAt: offsetIso(3 * 24 * 60),
      },
      {
        id: 2,
        limitType: "cost_usd",
        limitWindow: "monthly",
        maxValue: 2000,
        currentValue: 824,
        modelFilter: null,
        resetAt: offsetIso(30 * 24 * 60),
      },
    ],
  }),
  createApiKey({
    id: "key_dev",
    name: "Development",
    keyPrefix: "sk-dev",
    allowedModels: ["gpt-5.2-codex", "gpt-5.1-codex-mini"],
    isActive: true,
    expiresAt: offsetIso(30 * 24 * 60),
    lastUsedAt: offsetIso(-15),
    limits: [
      {
        id: 3,
        limitType: "total_tokens",
        limitWindow: "daily",
        maxValue: 200_000_000,
        currentValue: 68_500_000,
        modelFilter: null,
        resetAt: offsetIso(24 * 60),
      },
    ],
  }),
  createApiKey({
    id: "key_readonly",
    name: "Read-only testing",
    keyPrefix: "sk-ro",
    allowedModels: ["gpt-5.1-codex-mini"],
    isActive: false,
    expiresAt: offsetIso(-7 * 24 * 60),
    lastUsedAt: offsetIso(-14 * 24 * 60),
    limits: [],
  }),
];

// ── Sparkline trends (28 points = 7d × 4/day) ──
// Pattern mirrors real W04: slow start → build → massive Sat peak → wind-down

const requestsTrend = [
  // Mon: moderate start
  560, 1260, 80, 40,
  // Tue: morning spike
  1180, 380, 60, 20,
  // Wed: building
  350, 1190, 760, 30,
  // Thu: sustained
  1540, 1100, 300, 80,
  // Fri: heavy
  1280, 2440, 1140, 180,
  // Sat: peak
  4580, 4040, 580, 60,
  // Sun: wind-down
  760, 340, 40, 20,
].map((v, i) => ({ t: trendIso((28 - i) * 6), v }));

const tokensTrend = [
  // Mon
  32e6, 92e6, 6e6, 3e6,
  // Tue
  98e6, 22e6, 4e6, 1e6,
  // Wed
  13e6, 62e6, 48e6, 2e6,
  // Thu
  132e6, 100e6, 24e6, 5e6,
  // Fri
  108e6, 212e6, 122e6, 14e6,
  // Sat: peak
  428e6, 400e6, 46e6, 9e6,
  // Sun
  64e6, 28e6, 3e6, 1e6,
].map((v, i) => ({ t: trendIso((28 - i) * 6), v }));

const costTrend = [
  // Mon
  8.5, 24.0, 1.5, 0.8,
  // Tue
  26.0, 5.8, 1.0, 0.3,
  // Wed
  3.5, 16.5, 12.5, 0.5,
  // Thu
  35.0, 26.5, 6.2, 1.3,
  // Fri
  28.5, 56.0, 32.0, 3.6,
  // Sat: peak
  112.0, 105.0, 12.0, 2.2,
  // Sun
  17.0, 7.2, 0.8, 0.3,
].map((v, i) => ({ t: trendIso((28 - i) * 6), v }));

// Error rate: brief spikes during high-traffic windows, mostly clean
const errorRateTrend = [
  // Mon
  0.022, 0.038, 0.0, 0.0,
  // Tue
  0.001, 0.0, 0.0, 0.0,
  // Wed
  0.0, 0.015, 0.005, 0.0,
  // Thu
  0.009, 0.004, 0.0, 0.0,
  // Fri
  0.002, 0.001, 0.002, 0.0,
  // Sat: brief spike under load
  0.002, 0.005, 0.0, 0.0,
  // Sun
  0.001, 0.0, 0.0, 0.0,
].map((v, i) => ({ t: trendIso((28 - i) * 6), v }));

// ── Dashboard overview — derived from real W04 (~10-15% adjusted) ──

const totalPrimaryCap = 6 * PLUS_CAP_PRIMARY + PRO_CAP_PRIMARY;
const totalSecondaryCap = 6 * PLUS_CAP_SECONDARY + PRO_CAP_SECONDARY;

// Secondary remaining: pro had ~34% left, plus accounts 95-100%
const secondaryRemaining = [
  Math.round(PRO_CAP_SECONDARY * 0.34),   // acc_01 (pro): 17136
  Math.round(PLUS_CAP_SECONDARY * 0.97),  // acc_02: 7333
  Math.round(PLUS_CAP_SECONDARY * 0.95),  // acc_03: 7182
  Math.round(PLUS_CAP_SECONDARY * 0.97),  // acc_04: 7333
  Math.round(PLUS_CAP_SECONDARY * 0.96),  // acc_05: 7258
  PLUS_CAP_SECONDARY,                     // acc_06: 7560
  PLUS_CAP_SECONDARY,                     // acc_07: 7560
];
const totalSecondaryRemaining = secondaryRemaining.reduce((a, b) => a + b, 0);

export const overview = createDashboardOverview({
  accounts,
  summary: {
    primaryWindow: {
      remainingPercent: 100,
      capacityCredits: totalPrimaryCap,
      remainingCredits: totalPrimaryCap,
      resetAt: offsetIso(42),
      windowMinutes: 300,
    },
    secondaryWindow: {
      remainingPercent: Math.round((totalSecondaryRemaining / totalSecondaryCap) * 1000) / 10,
      capacityCredits: totalSecondaryCap,
      remainingCredits: totalSecondaryRemaining,
      resetAt: offsetIso(3 * 24 * 60),
      windowMinutes: 10_080,
    },
    cost: { currency: "USD", totalUsd7d: 486.72 },
    metrics: {
      requests7d: 22_480,
      tokensSecondaryWindow: 1_918_000_000,
      cachedTokensSecondaryWindow: 1_831_000_000,
      errorRate7d: 0.008,
      topError: "upstream_error",
    },
  },
  windows: {
    primary: {
      windowKey: "primary",
      windowMinutes: 300,
      accounts: accounts.map((a) => {
        const cap = a.planType === "pro" ? PRO_CAP_PRIMARY : PLUS_CAP_PRIMARY;
        return {
          accountId: a.accountId,
          remainingPercentAvg: 100,
          capacityCredits: cap,
          remainingCredits: cap,
        };
      }),
    },
    secondary: {
      windowKey: "secondary",
      windowMinutes: 10_080,
      accounts: accounts.map((a, i) => {
        const cap = a.planType === "pro" ? PRO_CAP_SECONDARY : PLUS_CAP_SECONDARY;
        const remaining = secondaryRemaining[i];
        return {
          accountId: a.accountId,
          remainingPercentAvg: Math.round((remaining / cap) * 100),
          capacityCredits: cap,
          remainingCredits: remaining,
        };
      }),
    },
  },
  trends: {
    requests: requestsTrend,
    tokens: tokensTrend,
    cost: costTrend,
    errorRate: errorRateTrend,
  },
});

// ── Auth / settings ──

export const authSession = createDashboardAuthSession({
  authenticated: true,
  passwordRequired: true,
  totpRequiredOnLogin: false,
  totpConfigured: true,
});

export const unauthenticatedSession = createDashboardAuthSession({
  authenticated: false,
  passwordRequired: true,
  totpRequiredOnLogin: false,
  totpConfigured: false,
});

export const settings = createDashboardSettings();

export const filterOptions = createRequestLogFilterOptions({
  accountIds: accounts.map((a) => a.accountId),
  modelOptions: [
    { model: "gpt-5.1-codex-mini", reasoningEffort: "medium" },
    { model: "gpt-5.2", reasoningEffort: "high" },
    { model: "gpt-5.2-codex", reasoningEffort: "high" },
    { model: "gpt-5.2-codex", reasoningEffort: "medium" },
  ],
  statuses: ["ok", "error"],
});

export const models = [
  { id: "gpt-5.2-codex", name: "GPT 5.2 Codex" },
  { id: "gpt-5.2", name: "GPT 5.2" },
  { id: "gpt-5.1-codex-mini", name: "GPT 5.1 Codex Mini" },
];

export { createRequestLogsResponse };
