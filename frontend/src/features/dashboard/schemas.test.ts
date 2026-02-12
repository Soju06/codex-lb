import { describe, expect, it } from "vitest";

import {
  AccountSummarySchema,
  DashboardOverviewSchema,
  RequestLogsResponseSchema,
  UsageWindowSchema,
} from "@/features/dashboard/schemas";

const ISO = "2026-01-01T00:00:00+00:00";

describe("DashboardOverviewSchema", () => {
  it("parses overview payload without request_logs", () => {
    const parsed = DashboardOverviewSchema.parse({
      lastSyncAt: ISO,
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
          totalUsd7d: 12.5,
        },
        metrics: {
          requests7d: 500,
          tokensSecondaryWindow: 2000,
          cachedTokensSecondaryWindow: 300,
          errorRate7d: 0.02,
          topError: null,
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
    });

    expect(parsed.accounts).toHaveLength(0);
  });

  it("drops legacy request_logs field from parse result", () => {
    const parsed = DashboardOverviewSchema.parse({
      lastSyncAt: ISO,
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
          totalUsd7d: 0,
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
      request_logs: [{ request_id: "legacy-row" }],
    });

    expect(parsed).not.toHaveProperty("request_logs");
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
});

describe("AccountSummarySchema light contract", () => {
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
