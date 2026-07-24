import { describe, expect, it } from "vitest";

import {
  AccountSummarySchema,
  AccountAdditionalQuotaSchema,
  ConversationDetailsSchema,
  ConversationEntrySchema,
  ConversationFilterStateSchema,
  ConversationsResponseSchema,
  DEFAULT_OVERVIEW_TIMEFRAME,
  DashboardOverviewSchema,
  DepletionSchema,
  FilterStateSchema,
  parseDashboardView,
  parseOverviewTimeframe,
  RequestLogFilterOptionsSchema,
  RequestLogsResponseSchema,
  UsageWindowSchema,
} from "@/features/dashboard/schemas";

const ISO = "2026-01-01T00:00:00+00:00";

const EMPTY_TRENDS = {
  requests: [],
  tokens: [],
  cost: [],
  errorRate: [],
};

describe("DashboardOverviewSchema", () => {
  it("parses overview payload without request_logs", () => {
    const parsed = DashboardOverviewSchema.parse({
      lastSyncAt: ISO,
      timeframe: {
        key: "7d",
        windowMinutes: 10080,
        bucketSeconds: 21600,
        bucketCount: 28,
      },
      accounts: [],
      summary: {
        primaryWindow: {
          remainingPercent: 80,
          capacityCredits: 100,
          remainingCredits: 80,
          resetAt: ISO,
          windowMinutes: 300,
        },
        secondaryWindow: null,
        cost: {
          currency: "USD",
          totalUsd: 12.5,
        },
        metrics: {
          requests: 500,
          tokens: 2000,
          cachedInputTokens: 300,
          errorRate: 0.02,
          errorCount: 10,
          topError: null,
        },
        comparison: {
          canCompare: true,
          previous: {
            requests: 250,
            tokens: 1000,
            costUsd: 6.25,
          },
        },
      },
      windows: {
        primary: {
          windowKey: "primary",
          windowMinutes: 300,
          accounts: [],
        },
        secondary: null,
      },
      trends: EMPTY_TRENDS,
    });

    expect(parsed.accounts).toHaveLength(0);
    expect(parsed.summary.comparison?.previous.requests).toBe(250);
  });

  it("drops legacy request_logs field from parse result", () => {
    const parsed = DashboardOverviewSchema.parse({
      lastSyncAt: ISO,
      timeframe: {
        key: "7d",
        windowMinutes: 10080,
        bucketSeconds: 21600,
        bucketCount: 28,
      },
      accounts: [],
      summary: {
        primaryWindow: {
          remainingPercent: 70,
          capacityCredits: 100,
          remainingCredits: 70,
          resetAt: ISO,
          windowMinutes: 300,
        },
        secondaryWindow: null,
        cost: {
          currency: "USD",
          totalUsd: 0,
        },
        metrics: null,
      },
      windows: {
        primary: {
          windowKey: "primary",
          windowMinutes: 300,
          accounts: [],
        },
        secondary: null,
      },
      trends: EMPTY_TRENDS,
      request_logs: [{ request_id: "legacy-row" }],
    });

    expect(parsed).not.toHaveProperty("request_logs");
  });

  it("accepts overview payloads without comparison block for backward compatibility", () => {
    const parsed = DashboardOverviewSchema.parse({
      lastSyncAt: ISO,
      timeframe: {
        key: "7d",
        windowMinutes: 10080,
        bucketSeconds: 21600,
        bucketCount: 28,
      },
      accounts: [],
      summary: {
        primaryWindow: {
          remainingPercent: 70,
          capacityCredits: 100,
          remainingCredits: 70,
          resetAt: ISO,
          windowMinutes: 300,
        },
        secondaryWindow: null,
        cost: {
          currency: "USD",
          totalUsd: 0,
        },
        metrics: null,
      },
      windows: {
        primary: {
          windowKey: "primary",
          windowMinutes: 300,
          accounts: [],
        },
        secondary: null,
      },
      trends: EMPTY_TRENDS,
    });

    expect(parsed.summary.comparison).toBeUndefined();
  });
});

describe("RequestLogsResponseSchema", () => {
  it("requires total and hasMore metadata", () => {
    const parsed = RequestLogsResponseSchema.parse({
      requests: [],
      total: 0,
      hasMore: false,
    });

    expect(parsed.total).toBe(0);
    expect(parsed.hasMore).toBe(false);
  });

  it("rejects missing pagination metadata", () => {
    const result = RequestLogsResponseSchema.safeParse({
      requests: [],
    });

    expect(result.success).toBe(false);
  });

  it("parses request rows including apiKeyName", () => {
    const parsed = RequestLogsResponseSchema.parse({
      requests: [
        {
          requestedAt: ISO,
          accountId: "acc-1",
          planType: "plus",
          apiKeyName: "Key A",
          apiKeyId: "key-1",
          requestId: "req-1",
          archiveRequestId: "archive-req-1",
          model: "gpt-5.1",
          transport: "websocket",
          useragent: "Mozilla/5.0",
          useragentGroup: "Mozilla",
          clientIp: "203.0.113.7",
          status: "ok",
          errorCode: null,
          errorMessage: null,
          failurePhase: "status",
          failureDetail: "upstream_5xx",
          failureExceptionType: "ProxyResponseError",
          upstreamStatusCode: 503,
          upstreamErrorCode: "server_error",
          bridgeStage: "owner_forward_status",
          tokens: 10,
          inputTokens: 8,
          outputTokens: 2,
          outputTokensRaw: 2,
          reasoningTokens: 1,
          cachedInputTokens: 0,
          reasoningEffort: null,
          costUsd: 0.001,
          costBreakdown: {
            inputUsd: 0.0004,
            cachedInputUsd: 0,
            outputUsd: 0.0006,
            totalUsd: 0.001,
          },
          latencyMs: 42,
        },
      ],
      total: 1,
      hasMore: false,
    });

    expect(parsed.requests[0]?.apiKeyName).toBe("Key A");
    expect(parsed.requests[0]?.apiKeyId).toBe("key-1");
    expect(parsed.requests[0]?.archiveRequestId).toBe("archive-req-1");
    expect(parsed.requests[0]?.requestKind).toBe("normal");
    expect(parsed.requests[0]?.planType).toBe("plus");
    expect(parsed.requests[0]?.transport).toBe("websocket");
    expect(parsed.requests[0]?.useragent).toBe("Mozilla/5.0");
    expect(parsed.requests[0]?.useragentGroup).toBe("Mozilla");
    expect(parsed.requests[0]?.clientIp).toBe("203.0.113.7");
    expect(parsed.requests[0]?.failurePhase).toBe("status");
    expect(parsed.requests[0]?.failureDetail).toBe("upstream_5xx");
    expect(parsed.requests[0]?.failureExceptionType).toBe("ProxyResponseError");
    expect(parsed.requests[0]?.upstreamStatusCode).toBe(503);
    expect(parsed.requests[0]?.upstreamErrorCode).toBe("server_error");
    expect(parsed.requests[0]?.bridgeStage).toBe("owner_forward_status");
    expect(parsed.requests[0]?.inputTokens).toBe(8);
    expect(parsed.requests[0]?.outputTokens).toBe(2);
    expect(parsed.requests[0]?.outputTokensRaw).toBe(2);
    expect(parsed.requests[0]?.reasoningTokens).toBe(1);
    expect(parsed.requests[0]?.costBreakdown?.totalUsd).toBe(0.001);
  });

  it("keeps archiveRequestId optional for older request-log responses", () => {
    const parsed = RequestLogsResponseSchema.parse({
      requests: [
        {
          requestedAt: ISO,
          accountId: "acc-1",
          requestId: "req-1",
          model: "gpt-5.1",
          status: "ok",
          errorCode: null,
          errorMessage: null,
          tokens: 10,
          cachedInputTokens: null,
          reasoningEffort: null,
          costUsd: null,
          latencyMs: 42,
        },
      ],
      total: 1,
      hasMore: false,
    });

    expect(parsed.requests[0]?.archiveRequestId).toBeUndefined();
  });

  it("accepts legacy limit warmup request kind rows", () => {
    const parsed = RequestLogsResponseSchema.parse({
      requests: [
        {
          requestedAt: ISO,
          accountId: "acc-1",
          planType: "plus",
          apiKeyName: null,
          apiKeyId: null,
          requestId: "req-legacy-limit-warmup",
          requestKind: "limit_warmup",
          model: "gpt-5.1-codex-mini",
          transport: "http",
          status: "ok",
          errorCode: null,
          errorMessage: null,
          tokens: 1,
          inputTokens: 1,
          outputTokens: 0,
          cachedInputTokens: 0,
          reasoningEffort: null,
          costUsd: 0,
          latencyMs: 42,
        },
      ],
      total: 1,
      hasMore: false,
    });

    expect(parsed.requests[0]?.requestKind).toBe("limit_warmup");
  });

  it("defaults omitted cost fields to null for backward compatibility", () => {
    const parsed = RequestLogsResponseSchema.parse({
      requests: [
        {
          requestedAt: ISO,
          accountId: "acc-1",
          planType: "plus",
          apiKeyId: "key-1",
          requestId: "req-legacy-cost-fields",
          model: "gpt-5.1",
          transport: "websocket",
          status: "ok",
          errorCode: null,
          errorMessage: null,
          tokens: 10,
          cachedInputTokens: 0,
          reasoningEffort: null,
          costUsd: 0.001,
          latencyMs: 42,
        },
      ],
      total: 1,
      hasMore: false,
    });

    expect(parsed.requests[0]?.inputTokens).toBeNull();
    expect(parsed.requests[0]?.outputTokens).toBeNull();
    expect(parsed.requests[0]?.failurePhase).toBeNull();
    expect(parsed.requests[0]?.upstreamStatusCode).toBeNull();
    expect(parsed.requests[0]?.costBreakdown).toBeNull();
    expect(parsed.requests[0]?.apiKeyName).toBeNull();
    expect(parsed.requests[0]?.useragent).toBeNull();
    expect(parsed.requests[0]?.useragentGroup).toBeNull();
    expect(parsed.requests[0]?.clientIp).toBeNull();
  });

  it("accepts nullable user agent fields", () => {
    const parsed = RequestLogsResponseSchema.parse({
      requests: [
        {
          requestedAt: ISO,
          accountId: "acc-1",
          planType: "plus",
          apiKeyName: "Key A",
          apiKeyId: "key-1",
          requestId: "req-null-useragent",
          model: "gpt-5.1",
          transport: "websocket",
          useragent: null,
          useragentGroup: null,
          clientIp: null,
          status: "ok",
          errorCode: null,
          errorMessage: null,
          tokens: 10,
          cachedInputTokens: 0,
          reasoningEffort: null,
          costUsd: 0.001,
          latencyMs: 42,
        },
      ],
      total: 1,
      hasMore: false,
    });

    expect(parsed.requests[0]?.useragent).toBeNull();
    expect(parsed.requests[0]?.useragentGroup).toBeNull();
    expect(parsed.requests[0]?.clientIp).toBeNull();
  });

  it("parses row-level conversationId and response-level conversation", () => {
    const parsed = RequestLogsResponseSchema.parse({
      requests: [
        {
          requestedAt: ISO,
          accountId: "acc-1",
          requestId: "req-cid",
          model: "gpt-5.1",
          status: "ok",
          errorCode: null,
          errorMessage: null,
          tokens: 10,
          cachedInputTokens: 0,
          reasoningEffort: null,
          costUsd: 0.001,
          latencyMs: 42,
          conversationId: "conv_abc123",
        },
        {
          requestedAt: ISO,
          accountId: null,
          requestId: "req-no-cid",
          model: "gpt-5.1",
          status: "ok",
          errorCode: null,
          errorMessage: null,
          tokens: 5,
          cachedInputTokens: null,
          reasoningEffort: null,
          costUsd: null,
          latencyMs: 30,
        },
      ],
      total: 2,
      hasMore: false,
      conversation: {
        requestCount: 2,
        aggregatedCostUsd: 0.001,
      },
    });

    expect(parsed.requests[0]?.conversationId).toBe("conv_abc123");
    expect(parsed.requests[1]?.conversationId).toBeNull();
    expect(parsed.conversation?.requestCount).toBe(2);
    expect(parsed.conversation?.aggregatedCostUsd).toBe(0.001);
  });

  it("accepts null response-level conversation", () => {
    const parsed = RequestLogsResponseSchema.parse({
      requests: [
        {
          requestedAt: ISO,
          accountId: "acc-1",
          requestId: "req-cid-null",
          model: "gpt-5.1",
          status: "ok",
          errorCode: null,
          errorMessage: null,
          tokens: 10,
          cachedInputTokens: 0,
          reasoningEffort: null,
          costUsd: 0.001,
          latencyMs: 42,
        },
      ],
      total: 1,
      hasMore: false,
      conversation: null,
    });

    expect(parsed.conversation).toBeNull();
  });

  it("omits response-level conversation when absent", () => {
    const parsed = RequestLogsResponseSchema.parse({
      requests: [
        {
          requestedAt: ISO,
          accountId: "acc-1",
          requestId: "req-no-conv-key",
          model: "gpt-5.1",
          status: "ok",
          errorCode: null,
          errorMessage: null,
          tokens: 10,
          cachedInputTokens: 0,
          reasoningEffort: null,
          costUsd: 0.001,
          latencyMs: 42,
        },
      ],
      total: 1,
      hasMore: false,
    });

    expect(parsed.conversation).toBeNull();
  });

  it("defaults omitted nested cost breakdown fields to null", () => {
    const parsed = RequestLogsResponseSchema.parse({
      requests: [
        {
          requestedAt: ISO,
          accountId: "acc-1",
          planType: "plus",
          apiKeyName: "Key A",
          apiKeyId: "key-1",
          requestId: "req-partial-breakdown",
          model: "gpt-5.1",
          transport: "websocket",
          status: "ok",
          errorCode: null,
          errorMessage: null,
          tokens: 10,
          inputTokens: 8,
          outputTokens: 2,
          cachedInputTokens: 0,
          reasoningEffort: null,
          costUsd: 0.001,
          costBreakdown: {
            inputUsd: 0.0004,
            totalUsd: 0.001,
          },
          latencyMs: 42,
        },
      ],
      total: 1,
      hasMore: false,
    });

    expect(parsed.requests[0]?.costBreakdown?.inputUsd).toBe(0.0004);
    expect(parsed.requests[0]?.costBreakdown?.cachedInputUsd).toBeNull();
    expect(parsed.requests[0]?.costBreakdown?.outputUsd).toBeNull();
    expect(parsed.requests[0]?.costBreakdown?.totalUsd).toBe(0.001);
  });

  it("parses request-log filter options including API keys", () => {
    const parsed = RequestLogFilterOptionsSchema.parse({
      accountIds: ["acc-1"],
      apiKeys: [{ id: "key-1", name: "Key A", keyPrefix: "sk-key-a" }],
      modelOptions: [{ model: "gpt-5.1", reasoningEffort: null }],
      statuses: ["ok"],
    });

    expect(parsed.apiKeys[0]?.id).toBe("key-1");
    expect(parsed.apiKeys[0]?.keyPrefix).toBe("sk-key-a");
  });
});

describe("FilterStateSchema", () => {
  it("parses optional string conversationId from URL params", () => {
    const state = {
      search: "",
      timeframe: "all" as const,
      accountIds: [],
      apiKeyIds: [],
      modelOptions: [],
      statuses: [],
      conversationId: "conv_abc123",
      limit: 25,
      offset: 0,
    };
    const parsed = FilterStateSchema.parse(state);
    expect(parsed.conversationId).toBe("conv_abc123");
  });

  it("defaults conversationId to null when absent", () => {
    const state = {
      search: "",
      timeframe: "all" as const,
      accountIds: [],
      apiKeyIds: [],
      modelOptions: [],
      statuses: [],
      limit: 25,
      offset: 0,
    };
    const parsed = FilterStateSchema.parse(state);
    expect(parsed.conversationId).toBeNull();
  });
});

describe("overview timeframe parsing", () => {
  it("defaults invalid values to 7d", () => {
    expect(parseOverviewTimeframe("invalid")).toBe(DEFAULT_OVERVIEW_TIMEFRAME);
    expect(parseOverviewTimeframe(null)).toBe(DEFAULT_OVERVIEW_TIMEFRAME);
  });
});

describe("UsageWindowSchema", () => {
  it("parses usage window payload", () => {
    const parsed = UsageWindowSchema.parse({
      windowKey: "secondary",
      windowMinutes: 10080,
      accounts: [
        {
          accountId: "acc-1",
          remainingPercentAvg: 42.1,
          capacityCredits: 100,
          remainingCredits: 42,
        },
      ],
    });

    expect(parsed.accounts[0]?.accountId).toBe("acc-1");
  });

  it("allows nullable remaining percent values", () => {
    const parsed = UsageWindowSchema.parse({
      windowKey: "primary",
      windowMinutes: 300,
      accounts: [
        {
          accountId: "acc-weekly-only",
          remainingPercentAvg: null,
          capacityCredits: 0,
          remainingCredits: 0,
        },
      ],
    });

    expect(parsed.accounts[0]?.remainingPercentAvg).toBeNull();
  });
});

describe("AccountSummarySchema light contract", () => {
  it("keeps weekly credit budget fields for dashboard pace math", () => {
    const parsed = AccountSummarySchema.parse({
      accountId: "acc-1",
      email: "user@example.com",
      displayName: "User",
      planType: "pro",
      status: "active",
      capacityCreditsSecondary: 2000,
      remainingCreditsSecondary: 900,
    });

    expect(parsed.capacityCreditsSecondary).toBe(2000);
    expect(parsed.remainingCreditsSecondary).toBe(900);
  });

  it("does not expose removed legacy fields", () => {
    const parsed = AccountSummarySchema.parse({
      accountId: "acc-1",
      email: "user@example.com",
      displayName: "User",
      planType: "pro",
      status: "active",
      capacity_credits_primary: 500,
      remaining_credits_primary: 300,
      capacity_credits_secondary: 2000,
      remaining_credits_secondary: 900,
      last_refresh_at: ISO,
      deactivation_reason: "manual",
    });

    expect(parsed).not.toHaveProperty("capacity_credits_primary");
    expect(parsed).not.toHaveProperty("remaining_credits_primary");
    expect(parsed).not.toHaveProperty("capacity_credits_secondary");
    expect(parsed).not.toHaveProperty("remaining_credits_secondary");
    expect(parsed).not.toHaveProperty("last_refresh_at");
    expect(parsed).not.toHaveProperty("deactivation_reason");
  });
});

describe("AccountAdditionalQuotaSchema", () => {
  it("parses valid additional quota data", () => {
    const parsed = AccountAdditionalQuotaSchema.parse({
      limitName: "requests_per_minute",
      meteredFeature: "requests",
      primaryWindow: {
        usedPercent: 45.5,
        resetAt: 1704067200,
        windowMinutes: 60,
      },
      secondaryWindow: null,
    });

    expect(parsed.limitName).toBe("requests_per_minute");
    expect(parsed.meteredFeature).toBe("requests");
    expect(parsed.primaryWindow?.usedPercent).toBe(45.5);
    expect(parsed.secondaryWindow).toBeNull();
  });

  it("allows optional window fields", () => {
    const parsed = AccountAdditionalQuotaSchema.parse({
      limitName: "tokens_per_day",
      meteredFeature: "tokens",
    });

    expect(parsed.limitName).toBe("tokens_per_day");
    expect(parsed.primaryWindow).toBeUndefined();
    expect(parsed.secondaryWindow).toBeUndefined();
  });
});

describe("DepletionSchema", () => {
  it("parses all risk levels", () => {
    const riskLevels = ["safe", "warning", "danger", "critical"] as const;

    riskLevels.forEach((level) => {
      const parsed = DepletionSchema.parse({
        risk: 0.5,
        riskLevel: level,
        burnRate: 0.1,
        safeUsagePercent: 80,
        projectedExhaustionAt: ISO,
        secondsUntilExhaustion: 86400,
      });

      expect(parsed.riskLevel).toBe(level);
    });
  });

  it("allows nullable exhaustion fields", () => {
    const parsed = DepletionSchema.parse({
      risk: 0.2,
      riskLevel: "safe",
      burnRate: 0.05,
      safeUsagePercent: 90,
      projectedExhaustionAt: null,
      secondsUntilExhaustion: null,
    });

    expect(parsed.projectedExhaustionAt).toBeNull();
    expect(parsed.secondsUntilExhaustion).toBeNull();
  });
});

describe("DashboardOverviewSchema with additional quotas", () => {
  it("parses with additionalQuotas array", () => {
    const parsed = DashboardOverviewSchema.parse({
      lastSyncAt: ISO,
      timeframe: {
        key: "7d",
        windowMinutes: 10080,
        bucketSeconds: 21600,
        bucketCount: 28,
      },
      accounts: [],
      summary: {
        primaryWindow: {
          remainingPercent: 80,
          capacityCredits: 100,
          remainingCredits: 80,
          resetAt: ISO,
          windowMinutes: 300,
        },
        secondaryWindow: null,
        cost: {
          currency: "USD",
          totalUsd: 12.5,
        },
        metrics: null,
      },
      windows: {
        primary: {
          windowKey: "primary",
          windowMinutes: 300,
          accounts: [],
        },
        secondary: null,
      },
      trends: EMPTY_TRENDS,
      additionalQuotas: [
        {
          limitName: "requests_per_minute",
          meteredFeature: "requests",
          primaryWindow: {
            usedPercent: 50,
            resetAt: 1704067200,
            windowMinutes: 60,
          },
        },
      ],
      depletionPrimary: {
        risk: 0.3,
        riskLevel: "warning",
        burnRate: 0.1,
        safeUsagePercent: 80,
      },
      depletionSecondary: {
        risk: 0.6,
        riskLevel: "danger",
        burnRate: 0.2,
        safeUsagePercent: 50,
      },
    });

    expect(parsed.additionalQuotas).toHaveLength(1);
    expect(parsed.additionalQuotas[0]?.limitName).toBe("requests_per_minute");
    expect(parsed.depletionPrimary?.riskLevel).toBe("warning");
    expect(parsed.depletionSecondary?.riskLevel).toBe("danger");
  });

  it("defaults additionalQuotas to empty array for backward compatibility", () => {
    const parsed = DashboardOverviewSchema.parse({
      lastSyncAt: ISO,
      timeframe: {
        key: "7d",
        windowMinutes: 10080,
        bucketSeconds: 21600,
        bucketCount: 28,
      },
      accounts: [],
      summary: {
        primaryWindow: {
          remainingPercent: 80,
          capacityCredits: 100,
          remainingCredits: 80,
          resetAt: ISO,
          windowMinutes: 300,
        },
        secondaryWindow: null,
        cost: {
          currency: "USD",
          totalUsd: 12.5,
        },
        metrics: null,
      },
      windows: {
        primary: {
          windowKey: "primary",
          windowMinutes: 300,
          accounts: [],
        },
        secondary: null,
      },
      trends: EMPTY_TRENDS,
    });

    expect(parsed.additionalQuotas).toEqual([]);
  });
});

describe("ConversationsResponseSchema", () => {
  it("parses a conversation row with nullable representative/key fields", () => {
    const parsed = ConversationsResponseSchema.parse({
      conversations: [
        {
          conversationId: "conv_abc",
          lastRequest: ISO,
          representativeAccount: null,
          remainingAccountCount: 0,
          apiKeyId: null,
          apiKeyName: null,
          representativeModel: "gpt-5.1",
          remainingModelCount: 2,
          totalTokens: 1800,
          cachedInputTokens: 320,
          totalCostUsd: 0.0132,
        },
      ],
      total: 1,
      hasMore: false,
    });

    expect(parsed.conversations).toHaveLength(1);
    expect(parsed.conversations[0]?.conversationId).toBe("conv_abc");
    expect(parsed.conversations[0]?.representativeAccount).toBeNull();
    expect(parsed.conversations[0]?.apiKeyName).toBeNull();
    expect(parsed.conversations[0]?.remainingModelCount).toBe(2);
    expect(parsed.conversations[0]?.cachedInputTokens).toBe(320);
  });

  it("requires pagination metadata", () => {
    const result = ConversationsResponseSchema.safeParse({
      conversations: [],
    });

    expect(result.success).toBe(false);
  });

  it("parses a populated row with all fields present", () => {
    const parsed = ConversationsResponseSchema.parse({
      conversations: [
        {
          conversationId: "conv_full",
          lastRequest: ISO,
          representativeAccount: "acc_primary",
          remainingAccountCount: 1,
          apiKeyId: "key_1",
          apiKeyName: "Primary Key",
          representativeModel: "gpt-5.1",
          remainingModelCount: 0,
          totalTokens: 100,
          cachedInputTokens: 0,
          totalCostUsd: 0,
        },
      ],
      total: 1,
      hasMore: true,
    });

    expect(parsed.conversations[0]?.representativeAccount).toBe("acc_primary");
    expect(parsed.conversations[0]?.apiKeyName).toBe("Primary Key");
    expect(parsed.hasMore).toBe(true);
  });
});

describe("ConversationEntrySchema", () => {
  it("keeps representative account/id as nullable", () => {
    const parsed = ConversationEntrySchema.parse({
      conversationId: "c",
      lastRequest: ISO,
      representativeAccount: null,
      remainingAccountCount: 0,
      apiKeyId: null,
      apiKeyName: null,
      representativeModel: null,
      remainingModelCount: 0,
      totalTokens: 0,
      cachedInputTokens: 0,
      totalCostUsd: 0,
    });

    expect(parsed.representativeAccount).toBeNull();
    expect(parsed.apiKeyId).toBeNull();
    expect(parsed.representativeModel).toBeNull();
  });

  it("accepts null cached input totals from the list endpoint", () => {
    const parsed = ConversationEntrySchema.parse({
      conversationId: "c-null-cache",
      lastRequest: ISO,
      representativeAccount: "acc-1",
      remainingAccountCount: 0,
      apiKeyId: null,
      apiKeyName: null,
      representativeModel: "gpt-5.1",
      remainingModelCount: 0,
      totalTokens: 0,
      cachedInputTokens: null,
      totalCostUsd: 0,
    });

    expect(parsed.cachedInputTokens).toBeNull();
  });
});

describe("ConversationDetailsSchema", () => {
  it("parses metadata and model/effort rows", () => {
    const parsed = ConversationDetailsSchema.parse({
      conversationId: "conv_d",
      start: ISO,
      latest: ISO,
      accountCount: 3,
      totalElapsedTime: 4200,
      dominantUseragentGroup: "opencode",
      modelStats: [
        {
          modelEffort: { model: "gpt-5.1", reasoningEffort: "high" },
          reqs: 4,
          totalElapsedTime: 1200,
          totalInputTokens: 1000,
          cachedInputTokens: 200,
          totalOutputTokens: 300,
          totalCostUsd: 0.05,
        },
      ],
    });

    expect(parsed.accountCount).toBe(3);
    expect(parsed.totalElapsedTime).toBe(4200);
    expect(parsed.dominantUseragentGroup).toBe("opencode");
    expect(parsed.modelStats[0]?.modelEffort.model).toBe("gpt-5.1");
    expect(parsed.modelStats[0]?.modelEffort.reasoningEffort).toBe("high");
    expect(parsed.modelStats[0]?.reqs).toBe(4);
    expect(parsed.modelStats[0]?.cachedInputTokens).toBe(200);
  });

  it("accepts nullable dominant user-agent and reasoning effort", () => {
    const parsed = ConversationDetailsSchema.parse({
      conversationId: "conv_null",
      start: ISO,
      latest: ISO,
      accountCount: 1,
      totalElapsedTime: 0,
      dominantUseragentGroup: null,
      modelStats: [
        {
          modelEffort: { model: "gpt-5.1", reasoningEffort: null },
          reqs: 1,
          totalElapsedTime: 0,
          totalInputTokens: 0,
          cachedInputTokens: 0,
          totalOutputTokens: 0,
          totalCostUsd: 0,
        },
      ],
    });

    expect(parsed.dominantUseragentGroup).toBeNull();
    expect(parsed.modelStats[0]?.modelEffort.reasoningEffort).toBeNull();
  });

  it("accepts null cached input totals from the detail endpoint", () => {
    const parsed = ConversationDetailsSchema.parse({
      conversationId: "conv-null-cache",
      start: ISO,
      latest: ISO,
      accountCount: 1,
      totalElapsedTime: 0,
      dominantUseragentGroup: null,
      modelStats: [
        {
          modelEffort: { model: "gpt-5.1", reasoningEffort: null },
          reqs: 1,
          totalElapsedTime: 0,
          totalInputTokens: 0,
          cachedInputTokens: null,
          totalOutputTokens: 0,
          totalCostUsd: 0,
        },
      ],
    });

    expect(parsed.modelStats[0]?.cachedInputTokens).toBeNull();
  });

  it("defaults modelStats to an empty array", () => {
    const parsed = ConversationDetailsSchema.parse({
      conversationId: "conv_empty",
      start: ISO,
      latest: ISO,
      accountCount: 0,
      totalElapsedTime: 0,
      dominantUseragentGroup: null,
    });

    expect(parsed.modelStats).toEqual([]);
  });
});

describe("ConversationFilterStateSchema", () => {
  it("parses search, limit, offset, and timeframe", () => {
    const parsed = ConversationFilterStateSchema.parse({
      search: "opencode",
      limit: 25,
      offset: 0,
      timeframe: "7d",
    });

    expect(parsed.search).toBe("opencode");
    expect(parsed.limit).toBe(25);
    expect(parsed.offset).toBe(0);
    expect(parsed.timeframe).toBe("7d");
  });

  it("rejects invalid timeframe values and strips other request-log keys", () => {
    const result = ConversationFilterStateSchema.safeParse({
      search: "x",
      limit: 25,
      offset: 0,
      timeframe: "24h",
      accountId: ["acc_1"],
    });

    expect(result.success).toBe(false);
  });
});

describe("parseDashboardView", () => {
  it("defaults to request-logs", () => {
    expect(parseDashboardView(null)).toBe("request-logs");
    expect(parseDashboardView(undefined)).toBe("request-logs");
    expect(parseDashboardView("unknown")).toBe("request-logs");
  });

  it("returns conversations for the conversations value", () => {
    expect(parseDashboardView("conversations")).toBe("conversations");
  });
});
