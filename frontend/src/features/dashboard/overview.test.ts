import { describe, expect, it } from "vitest";

import { buildActivitySeries, buildQuotaColumns, summarizeRequestLogs } from "@/features/dashboard/overview";
import { createAccountSummary, createRequestLogEntry } from "@/test/mocks/factories";

describe("summarizeRequestLogs", () => {
  it("computes error share, average and nearest-rank p95 over known latencies", () => {
    const rows = [1000, 2000, 3000, 4000].map((latencyMs, index) =>
      createRequestLogEntry({ requestId: `req_${index}`, latencyMs, status: index === 0 ? "error" : "ok" }),
    );
    rows.push(createRequestLogEntry({ requestId: "req_null", latencyMs: null }));

    const summary = summarizeRequestLogs(rows);

    expect(summary.count).toBe(5);
    expect(summary.errorCount).toBe(1);
    expect(summary.errorShare).toBeCloseTo(0.2);
    expect(summary.avgLatencyMs).toBe(2500);
    expect(summary.p95LatencyMs).toBe(4000);
  });

  it("sums only known tokens and derives cache share from known input", () => {
    const summary = summarizeRequestLogs([
      createRequestLogEntry({ requestId: "a", inputTokens: 100, outputTokens: 10, cachedInputTokens: 25 }),
      createRequestLogEntry({ requestId: "b", inputTokens: null, outputTokens: null, cachedInputTokens: null }),
      createRequestLogEntry({ requestId: "c", inputTokens: 300, outputTokens: 30, cachedInputTokens: null }),
    ]);

    expect(summary.inputTokens).toBe(400);
    expect(summary.outputTokens).toBe(40);
    expect(summary.cacheShare).toBeCloseTo(0.0625);
  });

  it("reports unknown figures as null", () => {
    expect(summarizeRequestLogs([])).toEqual({
      count: 0,
      errorCount: 0,
      errorShare: null,
      avgLatencyMs: null,
      p95LatencyMs: null,
      inputTokens: null,
      outputTokens: null,
      cacheShare: null,
    });
    const zeroInput = summarizeRequestLogs([createRequestLogEntry({ inputTokens: 0, cachedInputTokens: 0 })]);
    expect(zeroInput.inputTokens).toBe(0);
    expect(zeroInput.cacheShare).toBeNull();
    const unknownCache = summarizeRequestLogs([createRequestLogEntry({ inputTokens: 300, cachedInputTokens: null })]);
    expect(unknownCache.inputTokens).toBe(300);
    expect(unknownCache.cacheShare).toBeNull();
    expect(summarizeRequestLogs([createRequestLogEntry({ inputTokens: 300, cachedInputTokens: 0 })]).cacheShare).toBe(0);
  });
});

describe("buildQuotaColumns", () => {
  it("orders by used percent, fills tenths rounded up, and skips unknown values", () => {
    const accounts = [
      createAccountSummary({ accountId: "full", usage: { primaryRemainingPercent: 100, secondaryRemainingPercent: null, monthlyRemainingPercent: null } }),
      createAccountSummary({ accountId: "half", usage: { primaryRemainingPercent: 40, secondaryRemainingPercent: null, monthlyRemainingPercent: null } }),
      createAccountSummary({ accountId: "unknown", usage: { primaryRemainingPercent: null, secondaryRemainingPercent: 50, monthlyRemainingPercent: null } }),
      createAccountSummary({ accountId: "light", usage: { primaryRemainingPercent: 97, secondaryRemainingPercent: null, monthlyRemainingPercent: null } }),
    ];

    expect(buildQuotaColumns(accounts, "primary").map(({ accountId, usedPercent, filled }) => [accountId, usedPercent, filled])).toEqual([
      ["half", 60, 6],
      ["light", 3, 1],
      ["full", 0, 0],
    ]);
    expect(buildQuotaColumns(accounts, "secondary").map((column) => column.accountId)).toEqual(["unknown"]);
  });
});

describe("buildActivitySeries", () => {
  it("derives errors per bucket from requests and error rate", () => {
    const t = "2026-10-26T00:00:00Z";
    expect(
      buildActivitySeries({
        requests: [{ t, v: 200 }],
        tokens: [{ t, v: 5000 }],
        cost: [{ t, v: 1 }],
        errorRate: [{ t, v: 0.05 }],
        conversations: [],
      }),
    ).toEqual([{ t, requests: 200, errors: 10, tokens: 5000, errorRate: 0.05 }]);
  });
});
