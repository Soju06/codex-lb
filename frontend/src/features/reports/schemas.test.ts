import { describe, expect, it } from "vitest";

import { ReportsResponseSchema } from "./schemas";

function validReportsPayload() {
  return {
    summary: {
      totalCostUsd: 12.5, totalInputTokens: 300, totalOutputTokens: 200,
      totalReasoningTokens: 70, reasoningUsageKnownRequests: 3,
      totalCachedTokens: 0, totalRequests: 4, totalCancelled: 2,
      totalErrors: 1, totalConversations: 7, activeAccounts: 3,
      avgCostPerDay: 4.17, avgRequestsPerDay: 8.33,
    },
    comparison: { canCompare: true, previous: { totalCostUsd: 10, totalTokens: 400, totalRequests: 20 } },
    daily: [{ date: "2026-06-05", requests: 4, conversations: 3, inputTokens: 100, outputTokens: 50, reasoningTokens: 35, cachedInputTokens: 0, costUsd: 1, activeAccounts: 2, cancelledCount: 2, errorCount: 1 }],
    byModel: [{ model: "gpt-5.1", costUsd: 12.5, requests: 4, percentage: 100 }],
    byUseragent: [{ useragent: "claude-code", costUsd: 12.5, requests: 4, percentage: 100 }],
    byAccount: [],
  };
}

describe("ReportsResponseSchema", () => {
  it("preserves conversation, cancellation, and reasoning totals from the reports payload", () => {
    const parsed = ReportsResponseSchema.parse({
      summary: {
        totalCostUsd: 12.5, totalInputTokens: 300, totalOutputTokens: 200,
        totalReasoningTokens: 70, reasoningUsageKnownRequests: 3,
        totalCachedTokens: 0, totalRequests: 4, totalErrors: 1,
        totalCancelled: 2, totalConversations: 7, activeAccounts: 3,
        avgCostPerDay: 4.17, avgRequestsPerDay: 8.33,
      },
      comparison: { canCompare: true, previous: { totalCostUsd: 10, totalTokens: 400, totalRequests: 20 } },
      daily: [{ date: "2026-06-05", requests: 4, conversations: 3, inputTokens: 100, outputTokens: 50, reasoningTokens: 35, cachedInputTokens: 0, costUsd: 1, activeAccounts: 2, errorCount: 1, cancelledCount: 2 }],
      byModel: [{ model: "gpt-5.1", costUsd: 12.5, requests: 4, percentage: 100 }],
      byUseragent: [{ useragent: "claude-code", costUsd: 12.5, requests: 4, percentage: 100 }],
      byAccount: [],
    });
    expect(parsed.summary.totalRequests).toBe(4);
    expect(parsed.summary.totalErrors).toBe(1);
    expect.soft(Reflect.get(parsed.summary, "totalCancelled")).toBe(2);
    expect(parsed.summary.totalConversations).toBe(7);
    expect(parsed.summary.totalReasoningTokens).toBe(70);
    expect(parsed.summary.reasoningUsageKnownRequests).toBe(3);
    expect(parsed.daily[0]?.requests).toBe(4);
    expect(parsed.daily[0]?.errorCount).toBe(1);
    expect.soft(Reflect.get(parsed.daily[0] ?? {}, "cancelledCount")).toBe(2);
    expect(parsed.daily[0]?.conversations).toBe(3);
    expect(parsed.daily[0]?.reasoningTokens).toBe(35);
  });

  it("rejects omitted totalCancelled on summary", () => {
    const payload = validReportsPayload();
    Reflect.deleteProperty(payload.summary, "totalCancelled");

    expect(() => ReportsResponseSchema.parse(payload)).toThrow(/totalCancelled/i);
  });

  it("rejects omitted cancelledCount on daily rows", () => {
    const payload = validReportsPayload();
    Reflect.deleteProperty(payload.daily[0] ?? {}, "cancelledCount");

    expect(() => ReportsResponseSchema.parse(payload)).toThrow(/cancelledCount/i);
  });

  it("rejects omitted reasoning totals and coverage on summary", () => {
    const missingTotal = validReportsPayload();
    Reflect.deleteProperty(missingTotal.summary, "totalReasoningTokens");
    expect(() => ReportsResponseSchema.parse(missingTotal)).toThrow(/totalReasoningTokens/i);

    const missingCoverage = validReportsPayload();
    Reflect.deleteProperty(missingCoverage.summary, "reasoningUsageKnownRequests");
    expect(() => ReportsResponseSchema.parse(missingCoverage)).toThrow(/reasoningUsageKnownRequests/i);
  });

  it("rejects omitted reasoningTokens on daily rows", () => {
    const payload = validReportsPayload();
    Reflect.deleteProperty(payload.daily[0] ?? {}, "reasoningTokens");

    expect(() => ReportsResponseSchema.parse(payload)).toThrow(/reasoningTokens/i);
  });

  it("preserves null reasoningTokens as unknown on daily rows", () => {
    const payload = validReportsPayload();
    Reflect.set(payload.daily[0] ?? {}, "reasoningTokens", null);

    const parsed = ReportsResponseSchema.parse(payload);
    expect(parsed.daily[0]?.reasoningTokens).toBeNull();
  });

  it("rejects omitted totalConversations on summary", () => {
    expect(() =>
      ReportsResponseSchema.parse({
        summary: {
          totalCostUsd: 12.5, totalInputTokens: 300, totalOutputTokens: 200,
          totalReasoningTokens: 70, reasoningUsageKnownRequests: 3,
          totalCachedTokens: 0, totalRequests: 25, totalCancelled: 0, totalErrors: 1,
          activeAccounts: 3, avgCostPerDay: 4.17, avgRequestsPerDay: 8.33,
        },
        comparison: { canCompare: true, previous: { totalCostUsd: 10, totalTokens: 400, totalRequests: 20 } },
        daily: [{ date: "2026-06-05", requests: 10, conversations: 0, inputTokens: 100, outputTokens: 50, reasoningTokens: 35, cachedInputTokens: 0, costUsd: 1, activeAccounts: 2, cancelledCount: 0, errorCount: 0 }],
        byModel: [{ model: "gpt-5.1", costUsd: 12.5, requests: 25, percentage: 100 }],
        byUseragent: [{ useragent: "claude-code", costUsd: 12.5, requests: 25, percentage: 100 }],
        byAccount: [],
      }),
    ).toThrow(/totalConversations/i);
  });


  it("parses the required comparison block", () => {
    const parsed = ReportsResponseSchema.parse({
      summary: {
        totalCostUsd: 12.5,
        totalInputTokens: 300,
        totalOutputTokens: 200,
        totalReasoningTokens: 70,
        reasoningUsageKnownRequests: 3,
        totalCachedTokens: 0,
        totalRequests: 25,
        totalCancelled: 0,
        totalErrors: 1,
        totalConversations: 0,
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
          totalReasoningTokens: 70,
          reasoningUsageKnownRequests: 3,
          totalCachedTokens: 0,
          totalRequests: 25,
          totalCancelled: 0,
          totalErrors: 1,
          totalConversations: 0,
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
          totalReasoningTokens: 70,
          reasoningUsageKnownRequests: 3,
          totalCachedTokens: 0,
          totalRequests: 25,
          totalCancelled: 0,
          totalErrors: 1,
          totalConversations: 0,
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
          totalReasoningTokens: 70,
          reasoningUsageKnownRequests: 3,
          totalCachedTokens: 0,
          totalRequests: 25,
          totalCancelled: 0,
          totalErrors: 1,
          totalConversations: 0,
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
          totalReasoningTokens: 70,
          reasoningUsageKnownRequests: 3,
          totalCachedTokens: 0,
          totalRequests: 25,
          totalCancelled: 0,
          totalErrors: 1,
          totalConversations: 0,
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
