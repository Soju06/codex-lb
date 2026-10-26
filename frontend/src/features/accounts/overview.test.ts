import { describe, expect, it } from "vitest";

import { summarizeAccountsByPlan } from "@/features/accounts/overview";
import { createAccountSummary } from "@/test/mocks/factories";
import type { AccountSummary } from "@/features/accounts/schemas";

const NOW = Date.parse("2026-10-26T00:00:00Z");

function account(overrides: Partial<AccountSummary>): AccountSummary {
  return createAccountSummary({
    usage: { primaryRemainingPercent: 50, secondaryRemainingPercent: 50, monthlyRemainingPercent: null },
    resetAtPrimary: null,
    resetAtSecondary: null,
    ...overrides,
  });
}

function windowOf(rows: ReturnType<typeof summarizeAccountsByPlan>, plan: string | null, key: string) {
  return rows.find((row) => row.plan === plan)?.windows.find((window) => window.key === key);
}

describe("summarizeAccountsByPlan", () => {
  it("puts the all-plans row first, then plans by count and name, keeping prolite apart", () => {
    const rows = summarizeAccountsByPlan(
      [
        account({ accountId: "a", planType: "pro" }),
        account({ accountId: "b", planType: "plus" }),
        account({ accountId: "c", planType: "plus" }),
        account({ accountId: "d", planType: "prolite" }),
        account({ accountId: "e", planType: "plus" }),
      ],
      NOW,
    );

    expect(rows.map((row) => [row.plan, row.total])).toEqual([
      [null, 5],
      ["plus", 3],
      ["pro", 1],
      ["prolite", 1],
    ]);
  });

  it("counts every status and treats unknown statuses as active", () => {
    const statuses = ["active", "paused", "rate_limited", "quota_exceeded", "reauth_required", "deactivated", "mystery"];
    const rows = summarizeAccountsByPlan(
      statuses.map((status, index) => account({ accountId: `acc_${index}`, status })),
      NOW,
    );

    expect(rows[0].statusCounts).toEqual({
      active: 2,
      paused: 1,
      limited: 1,
      exceeded: 1,
      reauth: 1,
      deactivated: 1,
    });
  });

  it("averages known values, skips null, and bins boundaries into ten buckets", () => {
    const values = [100, 9.9, null, 0, 9.999, 10, 90, 150, -5];
    const rows = summarizeAccountsByPlan(
      values.map((value, index) =>
        account({
          accountId: `acc_${index}`,
          usage: { primaryRemainingPercent: value, secondaryRemainingPercent: null, monthlyRemainingPercent: null },
        }),
      ),
      NOW,
    );
    const primary = windowOf(rows, "plus", "primary");

    // 150 clamps to 100 and -5 clamps to 0.
    expect(primary?.known).toBe(8);
    expect(primary?.avgRemaining).toBeCloseTo((100 + 9.9 + 0 + 9.999 + 10 + 90 + 100 + 0) / 8);
    expect(primary?.lowCount).toBe(4);
    expect(primary?.bins).toEqual([4, 1, 0, 0, 0, 0, 0, 0, 0, 3]);
  });

  it("matches the spec example of 100, 9.9 and a missing value", () => {
    const rows = summarizeAccountsByPlan(
      [100, 9.9, null].map((value, index) =>
        account({
          accountId: `acc_${index}`,
          usage: { primaryRemainingPercent: value, secondaryRemainingPercent: 50, monthlyRemainingPercent: null },
        }),
      ),
      NOW,
    );
    const primary = windowOf(rows, "plus", "primary");

    expect(primary?.known).toBe(2);
    expect(primary?.avgRemaining).toBeCloseTo(54.95);
    expect(primary?.lowCount).toBe(1);
    expect(primary?.bins[9]).toBe(1);
  });

  it("reports a window with a length but no percentages as having no data", () => {
    const rows = summarizeAccountsByPlan(
      [
        account({
          accountId: "a",
          planType: "business",
          usage: { primaryRemainingPercent: 40, secondaryRemainingPercent: null, monthlyRemainingPercent: null },
          windowMinutesSecondary: 10_080,
        }),
      ],
      NOW,
    );
    const secondary = windowOf(rows, "business", "secondary");

    expect(secondary).toEqual({
      key: "secondary",
      reporting: 1,
      known: 0,
      avgRemaining: null,
      lowCount: 0,
      bins: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      nextResetAt: null,
    });
  });

  it("keeps monthly separate and omits windows nobody in the group reports", () => {
    const monthlyOnly = account({
      accountId: "free_1",
      planType: "free",
      usage: { primaryRemainingPercent: null, secondaryRemainingPercent: null, monthlyRemainingPercent: 30 },
      windowMinutesPrimary: null,
      windowMinutesSecondary: null,
      windowMinutesMonthly: 43_200,
    });
    const rows = summarizeAccountsByPlan([monthlyOnly, account({ accountId: "plus_1" })], NOW);

    expect(rows.find((row) => row.plan === "free")?.windows.map((window) => window.key)).toEqual(["monthly"]);
    expect(rows.find((row) => row.plan === "plus")?.windows.map((window) => window.key)).toEqual(["primary", "secondary"]);
    expect(rows[0].windows.map((window) => window.key)).toEqual(["primary", "secondary", "monthly"]);
    expect(windowOf(rows, null, "monthly")?.avgRemaining).toBe(30);
    expect(windowOf(rows, null, "primary")?.known).toBe(1);
    // The free account reports only monthly, so it is not counted against the 5-hour window.
    expect(windowOf(rows, null, "primary")?.reporting).toBe(1);
    expect(windowOf(rows, null, "monthly")?.reporting).toBe(1);
  });

  it("picks the earliest future reset and ignores past ones", () => {
    const rows = summarizeAccountsByPlan(
      [
        account({ accountId: "a", resetAtPrimary: "2026-10-25T23:00:00Z" }),
        account({ accountId: "b", resetAtPrimary: "2026-10-26T03:00:00Z" }),
        account({ accountId: "c", resetAtPrimary: "2026-10-26T01:00:00Z" }),
      ],
      NOW,
    );

    expect(windowOf(rows, "plus", "primary")?.nextResetAt).toBe("2026-10-26T01:00:00Z");
  });

  it("returns no rows for an empty pool", () => {
    expect(summarizeAccountsByPlan([], NOW)).toEqual([]);
  });
});
