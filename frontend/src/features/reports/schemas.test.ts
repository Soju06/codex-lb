import { describe, expect, it } from "vitest";

import { ReportsResponseSchema } from "./schemas";

describe("ReportsResponseSchema", () => {
  it("parses the required comparison block", () => {
    const parsed = ReportsResponseSchema.parse({
      summary: {
        totalCostUsd: 12.5,
        totalInputTokens: 300,
        totalOutputTokens: 200,
        totalCachedTokens: 0,
        totalRequests: 25,
        totalErrors: 1,
        activeAccounts: 3,
        avgCostPerDay: 4.17,
        avgRequestsPerDay: 8.33,
      },
      comparison: {
        canCompare: true,
        previous: {
          totalCostUsd: 10,
          totalTokens: 400,
          totalRequests: 20,
        },
      },
      daily: [],
      byModel: [
        {
          model: "gpt-5.1",
          costUsd: 12.5,
          requests: 25,
          percentage: 100,
        },
      ],
      byUseragent: [
        {
          useragent: "claude-code",
          costUsd: 12.5,
          requests: 25,
          percentage: 100,
        },
      ],
      byAccount: [],
    });

    expect(parsed.comparison.canCompare).toBe(true);
    expect(parsed.comparison.previous.totalCostUsd).toBe(10);
    expect(parsed.comparison.previous.totalTokens).toBe(400);
    expect(parsed.comparison.previous.totalRequests).toBe(20);
    expect(parsed.byModel[0]?.requests).toBe(25);
    expect(parsed.byUseragent[0]?.useragent).toBe("claude-code");
  });

  it("rejects payloads without the comparison block", () => {
    expect(() =>
      ReportsResponseSchema.parse({
        summary: {
          totalCostUsd: 12.5,
          totalInputTokens: 300,
          totalOutputTokens: 200,
          totalCachedTokens: 0,
          totalRequests: 25,
          totalErrors: 1,
          activeAccounts: 3,
          avgCostPerDay: 4.17,
          avgRequestsPerDay: 8.33,
        },
        daily: [],
        byModel: [],
        byUseragent: [],
        byAccount: [],
      }),
    ).toThrow(/comparison/i);
  });

  it("rejects comparison blocks without previous totals", () => {
    expect(() =>
      ReportsResponseSchema.parse({
        summary: {
          totalCostUsd: 12.5,
          totalInputTokens: 300,
          totalOutputTokens: 200,
          totalCachedTokens: 0,
          totalRequests: 25,
          totalErrors: 1,
          activeAccounts: 3,
          avgCostPerDay: 4.17,
          avgRequestsPerDay: 8.33,
        },
        comparison: {
          canCompare: false,
        },
        daily: [],
        byModel: [],
        byUseragent: [],
        byAccount: [],
      }),
    ).toThrow(/previous/i);
  });

  it("rejects byModel entries without request totals", () => {
    expect(() =>
      ReportsResponseSchema.parse({
        summary: {
          totalCostUsd: 12.5,
          totalInputTokens: 300,
          totalOutputTokens: 200,
          totalCachedTokens: 0,
          totalRequests: 25,
          totalErrors: 1,
          activeAccounts: 3,
          avgCostPerDay: 4.17,
          avgRequestsPerDay: 8.33,
        },
        comparison: {
          canCompare: true,
          previous: {
            totalCostUsd: 10,
            totalTokens: 400,
            totalRequests: 20,
          },
        },
        daily: [],
        byModel: [
          {
            model: "gpt-5.1",
            costUsd: 12.5,
            percentage: 100,
          },
        ],
        byUseragent: [],
        byAccount: [],
      }),
    ).toThrow(/requests/i);
  });

  it("rejects payloads without useragent breakdowns", () => {
    expect(() =>
      ReportsResponseSchema.parse({
        summary: {
          totalCostUsd: 12.5,
          totalInputTokens: 300,
          totalOutputTokens: 200,
          totalCachedTokens: 0,
          totalRequests: 25,
          totalErrors: 1,
          activeAccounts: 3,
          avgCostPerDay: 4.17,
          avgRequestsPerDay: 8.33,
        },
        comparison: {
          canCompare: true,
          previous: {
            totalCostUsd: 10,
            totalTokens: 400,
            totalRequests: 20,
          },
        },
        daily: [],
        byModel: [
          {
            model: "gpt-5.1",
            costUsd: 12.5,
            requests: 25,
            percentage: 100,
          },
        ],
        byAccount: [],
      }),
    ).toThrow(/byUseragent/i);
  });
});
