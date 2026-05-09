import { describe, expect, it } from "vitest";

import type { AccountSummary, Depletion } from "@/features/dashboard/schemas";
import {
  applySecondaryConstraint,
  buildDashboardView,
  buildDepletionView,
  buildRemainingItems,
  buildWeeklyCreditPace,
  sumRemaining,
  type RemainingItem,
} from "@/features/dashboard/utils";
import { createDashboardOverview, createDefaultRequestLogs } from "@/test/mocks/factories";
import { formatCompactAccountId } from "@/utils/account-identifiers";

function account(overrides: Partial<AccountSummary> & Pick<AccountSummary, "accountId" | "email">): AccountSummary {
  return {
    accountId: overrides.accountId,
    email: overrides.email,
    displayName: overrides.displayName ?? overrides.email,
    planType: overrides.planType ?? "plus",
    status: overrides.status ?? "active",
    usage: overrides.usage ?? null,
    resetAtPrimary: overrides.resetAtPrimary ?? null,
    resetAtSecondary: overrides.resetAtSecondary ?? null,
    windowMinutesPrimary: overrides.windowMinutesPrimary ?? null,
    windowMinutesSecondary: overrides.windowMinutesSecondary ?? null,
    capacityCreditsSecondary: overrides.capacityCreditsSecondary ?? null,
    remainingCreditsSecondary: overrides.remainingCreditsSecondary ?? null,
    auth: overrides.auth ?? null,
    additionalQuotas: overrides.additionalQuotas ?? [],
  };
}

describe("buildDepletionView", () => {
  it("returns null for null depletion", () => {
    expect(buildDepletionView(null)).toBeNull();
  });

  it("returns null for undefined depletion", () => {
    expect(buildDepletionView(undefined)).toBeNull();
  });

  it("returns null for safe risk level", () => {
    const depletion: Depletion = {
      risk: 0.1,
      riskLevel: "safe",
      burnRate: 0.5,
      safeUsagePercent: 90,
    };
    expect(buildDepletionView(depletion)).toBeNull();
  });

  it("returns view for warning risk level", () => {
    const depletion: Depletion = {
      risk: 0.5,
      riskLevel: "warning",
      burnRate: 1.5,
      safeUsagePercent: 45,
    };
    const view = buildDepletionView(depletion);
    expect(view).toEqual({
      safePercent: 45,
      riskLevel: "warning",
    });
  });

  it("returns view for danger risk level", () => {
    const depletion: Depletion = {
      risk: 0.75,
      riskLevel: "danger",
      burnRate: 2.5,
      safeUsagePercent: 30,
    };
    const view = buildDepletionView(depletion);
    expect(view).toEqual({
      safePercent: 30,
      riskLevel: "danger",
    });
  });

  it("returns view for critical risk level", () => {
    const depletion: Depletion = {
      risk: 0.95,
      riskLevel: "critical",
      burnRate: 5.0,
      safeUsagePercent: 20,
    };
    const view = buildDepletionView(depletion);
    expect(view).toEqual({
      safePercent: 20,
      riskLevel: "critical",
    });
  });
});

function remainingItem(overrides: Partial<RemainingItem> & Pick<RemainingItem, "accountId">): RemainingItem {
  return {
    accountId: overrides.accountId,
    label: overrides.label ?? overrides.accountId,
    labelSuffix: overrides.labelSuffix ?? "",
    isEmail: overrides.isEmail ?? false,
    value: overrides.value ?? 100,
    remainingPercent: overrides.remainingPercent === undefined ? 80 : overrides.remainingPercent,
    color: overrides.color ?? "#aaa",
  };
}

describe("applySecondaryConstraint", () => {
  it("no-op when 7d remaining credits >= 5h remaining credits", () => {
    const primary = [remainingItem({ accountId: "acc-1", value: 180, remainingPercent: 80 })];
    const secondary = [remainingItem({ accountId: "acc-1", value: 6000, remainingPercent: 79 })];

    const result = applySecondaryConstraint(primary, secondary);

    expect(result[0].value).toBe(180);
    expect(result[0].remainingPercent).toBe(80);
  });

  it("caps 5h to 7d absolute credits when 7d remaining < 5h remaining", () => {
    const primary = [remainingItem({ accountId: "acc-1", value: 200, remainingPercent: 90 })];
    const secondary = [remainingItem({ accountId: "acc-1", value: 75, remainingPercent: 1 })];

    const result = applySecondaryConstraint(primary, secondary);

    expect(result[0].value).toBe(75);
    expect(result[0].remainingPercent).toBeCloseTo(90 * (75 / 200));
  });

  it("zeros 5h when 7d is fully depleted", () => {
    const primary = [remainingItem({ accountId: "acc-1", value: 200, remainingPercent: 90 })];
    const secondary = [remainingItem({ accountId: "acc-1", value: 0, remainingPercent: 0 })];

    const result = applySecondaryConstraint(primary, secondary);

    expect(result[0].value).toBe(0);
    expect(result[0].remainingPercent).toBe(0);
  });

  it("no-op when 7d has plenty even with low percent (different capacity scales)", () => {
    const primary = [remainingItem({ accountId: "acc-1", value: 200, remainingPercent: 90 })];
    const secondary = [remainingItem({ accountId: "acc-1", value: 3780, remainingPercent: 50 })];

    const result = applySecondaryConstraint(primary, secondary);

    expect(result[0].value).toBe(200);
    expect(result[0].remainingPercent).toBe(90);
  });

  it("preserves null remainingPercent on capped items", () => {
    const primary = [remainingItem({ accountId: "acc-1", value: 200, remainingPercent: null })];
    const secondary = [remainingItem({ accountId: "acc-1", value: 50 })];

    const result = applySecondaryConstraint(primary, secondary);

    expect(result[0].value).toBe(50);
    expect(result[0].remainingPercent).toBeNull();
  });

  it("returns primary unchanged when no matching secondary account exists", () => {
    const primary = [remainingItem({ accountId: "acc-1", value: 200, remainingPercent: 90 })];
    const secondary = [remainingItem({ accountId: "acc-2", value: 0, remainingPercent: 0 })];

    const result = applySecondaryConstraint(primary, secondary);

    expect(result[0].value).toBe(200);
    expect(result[0].remainingPercent).toBe(90);
  });

  it("does not clamp primary when secondary data is missing", () => {
    const primary = [remainingItem({ accountId: "acc-1", value: 200, remainingPercent: 90 })];
    const secondary = [remainingItem({ accountId: "acc-1", value: 0, remainingPercent: null })];

    const result = applySecondaryConstraint(primary, secondary);

    expect(result[0].value).toBe(200);
    expect(result[0].remainingPercent).toBe(90);
  });

  it("handles multiple accounts independently", () => {
    const primary = [
      remainingItem({ accountId: "acc-1", value: 200, remainingPercent: 90 }),
      remainingItem({ accountId: "acc-2", value: 150, remainingPercent: 60 }),
    ];
    const secondary = [
      remainingItem({ accountId: "acc-1", value: 75, remainingPercent: 1 }),
      remainingItem({ accountId: "acc-2", value: 5000, remainingPercent: 70 }),
    ];

    const result = applySecondaryConstraint(primary, secondary);

    expect(result[0].value).toBe(75);
    expect(result[0].remainingPercent).toBeCloseTo(90 * (75 / 200));
    expect(result[1].value).toBe(150);
    expect(result[1].remainingPercent).toBe(60);
  });

  it("returns empty array when primary is empty", () => {
    const result = applySecondaryConstraint([], [remainingItem({ accountId: "acc-1" })]);
    expect(result).toEqual([]);
  });

  it("does not mutate original primary items", () => {
    const primary = [remainingItem({ accountId: "acc-1", value: 200, remainingPercent: 90 })];
    const secondary = [remainingItem({ accountId: "acc-1", value: 0, remainingPercent: 0 })];

    applySecondaryConstraint(primary, secondary);

    expect(primary[0].value).toBe(200);
    expect(primary[0].remainingPercent).toBe(90);
  });

  it("caps to zero when secondary items are all zero-valued", () => {
    const primary = [
      remainingItem({ accountId: "acc-1", value: 200, remainingPercent: 90 }),
      remainingItem({ accountId: "acc-2", value: 150, remainingPercent: 60 }),
    ];
    const secondary = [
      remainingItem({ accountId: "acc-1", value: 0, remainingPercent: 0 }),
      remainingItem({ accountId: "acc-2", value: 0, remainingPercent: 0 }),
    ];

    const result = applySecondaryConstraint(primary, secondary);

    expect(result[0].value).toBe(0);
    expect(result[1].value).toBe(0);
  });
});

describe("buildRemainingItems", () => {
  it("keeps default labels for non-duplicate accounts", () => {
    const items = buildRemainingItems(
      [
        account({ accountId: "acc-1", email: "one@example.com" }),
        account({ accountId: "acc-2", email: "two@example.com" }),
      ],
      null,
      "primary",
    );

    expect(items[0].label).toBe("one@example.com");
    expect(items[1].label).toBe("two@example.com");
  });

  it("appends compact account id only for duplicate emails", () => {
    const duplicateA = "d48f0bfc-8ea6-48a7-8d76-d0e5ef1816c5_6f12b5d5";
    const duplicateB = "7f9de2ad-7621-4a6f-88bc-ec7f3d914701_91a95cee";
    const items = buildRemainingItems(
      [
        account({ accountId: duplicateA, email: "dup@example.com" }),
        account({ accountId: duplicateB, email: "dup@example.com" }),
        account({ accountId: "acc-3", email: "unique@example.com" }),
      ],
      null,
      "primary",
    );

    expect(items[0].label).toBe("dup@example.com");
    expect(items[0].labelSuffix).toBe(` (${formatCompactAccountId(duplicateA, 5, 4)})`);
    expect(items[0].isEmail).toBe(true);
    expect(items[1].label).toBe("dup@example.com");
    expect(items[1].labelSuffix).toBe(` (${formatCompactAccountId(duplicateB, 5, 4)})`);
    expect(items[1].isEmail).toBe(true);
    expect(items[2].label).toBe("unique@example.com");
    expect(items[2].labelSuffix).toBe("");
    expect(items[2].isEmail).toBe(true);
  });
});

describe("sumRemaining", () => {
  it("returns 0 for empty array", () => {
    expect(sumRemaining([])).toBe(0);
  });

  it("sums positive values", () => {
    const items = [
      remainingItem({ accountId: "a", value: 120 }),
      remainingItem({ accountId: "b", value: 80 }),
    ];
    expect(sumRemaining(items)).toBe(200);
  });

  it("clamps negative values to 0 before summing", () => {
    const items = [
      remainingItem({ accountId: "a", value: 100 }),
      remainingItem({ accountId: "b", value: -30 }),
    ];
    expect(sumRemaining(items)).toBe(100);
  });

  it("returns 0 when all values are negative", () => {
    const items = [
      remainingItem({ accountId: "a", value: -10 }),
      remainingItem({ accountId: "b", value: -20 }),
    ];
    expect(sumRemaining(items)).toBe(0);
  });
});

describe("buildWeeklyCreditPace", () => {
  const now = new Date("2026-01-07T12:00:00Z");

  type WeeklyAccountOverrides = Partial<AccountSummary> & {
    accountId: string;
    fullCredits?: number | null;
    remainingCredits?: number | null;
    timeLeftPercent?: number;
  };

  function weeklyAccount(overrides: WeeklyAccountOverrides): AccountSummary {
    const { accountId, fullCredits, remainingCredits, timeLeftPercent: timeLeftOverride, ...accountOverrides } = overrides;
    const windowMinutes = 10_080;
    const timeLeftPercent = timeLeftOverride ?? 50;
    const resetAt = new Date(now.getTime() + windowMinutes * 60_000 * (timeLeftPercent / 100)).toISOString();
    const fullCreditBudget = fullCredits !== undefined ? fullCredits : accountOverrides.capacityCreditsSecondary ?? 100_000;
    const remainingCreditBudget =
      remainingCredits !== undefined ? remainingCredits : accountOverrides.remainingCreditsSecondary ?? 50_000;
    return account({
      ...accountOverrides,
      accountId,
      email: `${accountId}@example.com`,
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent:
          fullCreditBudget && remainingCreditBudget != null
            ? (remainingCreditBudget / fullCreditBudget) * 100
            : null,
      },
      resetAtSecondary: accountOverrides.resetAtSecondary !== undefined ? accountOverrides.resetAtSecondary : resetAt,
      windowMinutesSecondary: accountOverrides.windowMinutesSecondary !== undefined
        ? accountOverrides.windowMinutesSecondary
        : windowMinutes,
      capacityCreditsSecondary: fullCreditBudget,
      remainingCreditsSecondary: remainingCreditBudget,
    });
  }

  it("treats a 99% used account at 99% elapsed as on pace", () => {
    const pace = buildWeeklyCreditPace(
      [weeklyAccount({ accountId: "acc-close", fullCredits: 100_000, remainingCredits: 1_000, timeLeftPercent: 1 })],
      now,
    );

    expect(pace).not.toBeNull();
    expect(pace?.totalExpectedRemainingCredits).toBeCloseTo(1_000);
    expect(pace?.overPlanCredits).toBeCloseTo(0);
    expect(pace?.deltaPercent).toBeCloseTo(0);
    expect(pace?.pauseForBreakEvenHours).toBeNull();
    expect(pace?.paceMultiplier).toBeNull();
    expect(pace?.throttleToPercent).toBeNull();
    expect(pace?.reduceByPercent).toBeNull();
    expect(pace?.proAccountsToCoverOverPlan).toBeNull();
    expect(pace?.status).toBe("on_track");
  });

  it("does not report a shortfall when the weekly reset replenishes a sustainable account", () => {
    const pace = buildWeeklyCreditPace(
      [weeklyAccount({ accountId: "acc-sustainable", fullCredits: 100_000, remainingCredits: 50_000, timeLeftPercent: 50 })],
      now,
    );

    expect(pace).not.toBeNull();
    expect(pace?.overPlanCredits).toBeCloseTo(0);
    expect(pace?.projectedDepletionHours).toBeNull();
    expect(pace?.pauseForBreakEvenHours).toBeNull();
    expect(pace?.status).toBe("on_track");
  });

  it("aggregates credit budgets instead of averaging account percentages", () => {
    const pace = buildWeeklyCreditPace(
      [
        weeklyAccount({ accountId: "acc-small", fullCredits: 100_000, remainingCredits: 1_000, timeLeftPercent: 1 }),
        weeklyAccount({ accountId: "acc-large", fullCredits: 900_000, remainingCredits: 800_000, timeLeftPercent: 80 }),
      ],
      now,
    );

    expect(pace).not.toBeNull();
    expect(pace?.accountCount).toBe(2);
    expect(pace?.totalActualRemainingCredits).toBeCloseTo(801_000);
    expect(pace?.totalExpectedRemainingCredits).toBeCloseTo(721_000);
    expect(pace?.overPlanCredits).toBeCloseTo(0);
    expect(pace?.actualUsedPercent).toBeCloseTo(19.9);
    expect(pace?.scheduledUsedPercent).toBeCloseTo(27.9);
    expect(pace?.pauseForBreakEvenHours).toBeNull();
    expect(pace?.paceMultiplier).toBeNull();
    expect(pace?.proAccountsToCoverOverPlan).toBeNull();
    expect(pace?.status).toBe("behind");
  });

  it("marks a large account depleted too early as danger", () => {
    const pace = buildWeeklyCreditPace(
      [weeklyAccount({ accountId: "acc-early", fullCredits: 1_000_000, remainingCredits: 10_000, timeLeftPercent: 80 })],
      now,
    );

    expect(pace).not.toBeNull();
    expect(pace?.totalExpectedRemainingCredits).toBeCloseTo(800_000);
    expect(pace?.overPlanCredits).toBeCloseTo(3_950_000);
    expect(pace?.deltaPercent).toBeCloseTo(79);
    expect(pace?.pauseForBreakEvenHours).toBeCloseTo(134.06);
    expect(pace?.paceMultiplier).toBeCloseTo(4.95);
    expect(pace?.throttleToPercent).toBeCloseTo(0.25);
    expect(pace?.reduceByPercent).toBeCloseTo(99.75);
    expect(pace?.proAccountEquivalentToCoverOverPlan).toBeCloseTo(78.37);
    expect(pace?.proAccountsToCoverOverPlan).toBe(79);
    expect(pace?.projectedDepletionHours).toBeCloseTo(0.34);
    expect(pace?.status).toBe("danger");
  });

  it("uses each account reset time before summing credits", () => {
    const pace = buildWeeklyCreditPace(
      [
        weeklyAccount({ accountId: "acc-near", fullCredits: 100_000, remainingCredits: 50_000, timeLeftPercent: 10 }),
        weeklyAccount({ accountId: "acc-far", fullCredits: 100_000, remainingCredits: 50_000, timeLeftPercent: 90 }),
      ],
      now,
    );

    expect(pace).not.toBeNull();
    expect(pace?.totalExpectedRemainingCredits).toBeCloseTo(100_000);
    expect(pace?.scheduledUsedPercent).toBeCloseTo(50);
    expect(pace?.actualUsedPercent).toBeCloseTo(50);
    expect(pace?.overPlanCredits).toBeGreaterThan(0);
    expect(pace?.pauseForBreakEvenHours).toBeCloseTo(90.72);
    expect(pace?.paceMultiplier).toBeCloseTo(1);
    expect(pace?.proAccountEquivalentToCoverOverPlan).toBeGreaterThan(0);
    expect(pace?.proAccountsToCoverOverPlan).toBe(6);
    expect(pace?.status).toBe("danger");
  });

  it("expires unused account credits at reset instead of carrying them forward", () => {
    const pace = buildWeeklyCreditPace(
      [
        weeklyAccount({ accountId: "unused-near-reset", fullCredits: 1_000, remainingCredits: 1_000, timeLeftPercent: 10 }),
        weeklyAccount({ accountId: "empty-later-reset", fullCredits: 1_000, remainingCredits: 0, timeLeftPercent: 90 }),
      ],
      now,
    );

    expect(pace).not.toBeNull();
    expect(pace?.overPlanCredits).toBeCloseTo(0);
    expect(pace?.projectedMinimumRemainingCredits).toBeCloseTo(0);
  });

  it("does not let a tiny near reset hide depletion before the next meaningful reset", () => {
    const pace = buildWeeklyCreditPace(
      [
        weeklyAccount({ accountId: "tiny-reset", fullCredits: 2, remainingCredits: 0, timeLeftPercent: 1 }),
        weeklyAccount({ accountId: "large-later", fullCredits: 100_000, remainingCredits: 20_000, timeLeftPercent: 50 }),
      ],
      now,
    );

    expect(pace).not.toBeNull();
    expect(pace?.overPlanCredits).toBeCloseTo(59_999.01);
    expect(pace?.projectedDepletionHours).toBeLessThan(40);
    expect(pace?.proAccountEquivalentToCoverOverPlan).toBeGreaterThan(1);
    expect(pace?.proAccountsToCoverOverPlan).toBe(2);
    expect(pace?.status).toBe("danger");
  });

  it("computes break-even pause across different reset deadlines", () => {
    const pace = buildWeeklyCreditPace(
      [
        weeklyAccount({ accountId: "acc-near", fullCredits: 100_000, remainingCredits: 0, timeLeftPercent: 10 }),
        weeklyAccount({ accountId: "acc-far", fullCredits: 100_000, remainingCredits: 0, timeLeftPercent: 90 }),
      ],
      now,
    );

    expect(pace).not.toBeNull();
    expect(pace?.overPlanCredits).toBeCloseTo(111_111.11);
    expect(pace?.pauseForBreakEvenHours).toBeGreaterThan(0);
    expect(pace?.paceMultiplier).toBeCloseTo(2);
    expect(pace?.throttleToPercent).toBeCloseTo(0);
    expect(pace?.reduceByPercent).toBeCloseTo(100);
    expect(pace?.proAccountEquivalentToCoverOverPlan).toBeCloseTo(2.2);
    expect(pace?.proAccountsToCoverOverPlan).toBe(3);
    expect(pace?.projectedDepletionHours).toBeCloseTo(0);
    expect(pace?.status).toBe("danger");
  });

  it("shows recovery pressure when per-account weekly burn exceeds staggered resets", () => {
    const liveLikeNow = new Date("2026-05-03T19:19:35Z");
    const pace = buildWeeklyCreditPace(
      [
        account({
          accountId: "old-pro-1",
          email: "old-pro-1@example.com",
          planType: "pro",
          capacityCreditsSecondary: 50_400,
          remainingCreditsSecondary: 1_008,
          resetAtSecondary: "2026-05-05T05:34:05Z",
          windowMinutesSecondary: 10_080,
        }),
        account({
          accountId: "old-pro-2",
          email: "old-pro-2@example.com",
          planType: "pro",
          capacityCreditsSecondary: 50_400,
          remainingCreditsSecondary: 1_512,
          resetAtSecondary: "2026-05-05T05:51:53Z",
          windowMinutesSecondary: 10_080,
        }),
        account({
          accountId: "team-1",
          email: "team-1@example.com",
          planType: "team",
          capacityCreditsSecondary: 7_560,
          remainingCreditsSecondary: 1_587.6,
          resetAtSecondary: "2026-05-05T13:31:20Z",
          windowMinutesSecondary: 10_080,
        }),
        account({
          accountId: "team-2",
          email: "team-2@example.com",
          planType: "team",
          capacityCreditsSecondary: 7_560,
          remainingCreditsSecondary: 5_443.2,
          resetAtSecondary: "2026-05-06T14:45:02Z",
          windowMinutesSecondary: 10_080,
        }),
        account({
          accountId: "new-pro",
          email: "new-pro@example.com",
          planType: "pro",
          capacityCreditsSecondary: 50_400,
          remainingCreditsSecondary: 48_888,
          resetAtSecondary: "2026-05-10T18:19:10Z",
          windowMinutesSecondary: 10_080,
        }),
      ],
      liveLikeNow,
    );

    expect(pace).not.toBeNull();
    expect(pace?.accountCount).toBe(5);
    expect(pace?.totalActualRemainingCredits).toBeCloseTo(58_438.8);
    expect(pace?.overPlanCredits).toBeCloseTo(20_510.96);
    expect(pace?.pauseForBreakEvenHours).toBeGreaterThan(0);
    expect(pace?.paceMultiplier).toBeGreaterThan(1);
    expect(pace?.throttleToPercent).toBeGreaterThanOrEqual(0);
    expect(pace?.reduceByPercent).toBeGreaterThan(0);
    expect(pace?.proAccountEquivalentToCoverOverPlan).toBeGreaterThan(0);
    expect(pace?.proAccountsToCoverOverPlan).toBe(1);
    expect(pace?.projectedMinimumRemainingCredits).toBeCloseTo(0);
    expect(pace?.status).toBe("danger");
  });

  it("skips accounts without complete weekly credit timing data", () => {
    const pace = buildWeeklyCreditPace(
      [
        weeklyAccount({ accountId: "missing-full", fullCredits: null, remainingCredits: 1_000, timeLeftPercent: 50 }),
        weeklyAccount({ accountId: "missing-reset", fullCredits: 100_000, remainingCredits: 50_000, resetAtSecondary: null }),
        weeklyAccount({ accountId: "missing-window", fullCredits: 100_000, remainingCredits: 50_000, windowMinutesSecondary: null }),
      ],
      now,
    );

    expect(pace).toBeNull();
  });
});

describe("buildDashboardView", () => {
  it("keeps donut totals anchored to window capacity even when displayed slices are constrained", () => {
    const overview = createDashboardOverview({
      accounts: [
        account({
          accountId: "acc-1",
          email: "one@example.com",
          usage: {
            primaryRemainingPercent: 90,
            secondaryRemainingPercent: 1,
          },
          resetAtPrimary: null,
          resetAtSecondary: null,
          windowMinutesPrimary: 300,
          windowMinutesSecondary: 10080,
        }),
        account({
          accountId: "acc-2",
          email: "two@example.com",
          usage: {
            primaryRemainingPercent: 60,
            secondaryRemainingPercent: 70,
          },
          resetAtPrimary: null,
          resetAtSecondary: null,
          windowMinutesPrimary: 300,
          windowMinutesSecondary: 10080,
        }),
      ],
      summary: {
        primaryWindow: {
          remainingPercent: 75,
          capacityCredits: 450,
          remainingCredits: 337.5,
          resetAt: null,
          windowMinutes: 300,
        },
        secondaryWindow: {
          remainingPercent: 35.5,
          capacityCredits: 15120,
          remainingCredits: 5370,
          resetAt: null,
          windowMinutes: 10080,
        },
        cost: {
          currency: "USD",
          totalUsd: 1.82,
        },
        metrics: {
          requests: 228,
          tokens: 45000,
          cachedInputTokens: 8200,
          errorRate: 0.028,
          errorCount: 6,
          topError: "rate_limit_exceeded",
        },
      },
    });

    const view = buildDashboardView(overview, createDefaultRequestLogs(), false);

    expect(view.primaryUsageItems).toHaveLength(2);
    expect(view.primaryUsageItems[0]?.value).toBeCloseTo(75.6);
    expect(view.primaryUsageItems[1]?.value).toBeCloseTo(135);
    expect(overview.summary.primaryWindow.capacityCredits).toBe(450);
    expect(overview.summary.secondaryWindow?.capacityCredits).toBe(15120);
    expect(view.primaryUsageItems.reduce((total, item) => total + item.value, 0)).toBeCloseTo(210.6);
  });

  it("keeps primary totals intact for accounts without secondary usage data", () => {
    const overview = createDashboardOverview({
      accounts: [
        account({
          accountId: "acc-1",
          email: "one@example.com",
          usage: {
            primaryRemainingPercent: 90,
            secondaryRemainingPercent: null,
          },
          resetAtPrimary: null,
          resetAtSecondary: null,
          windowMinutesPrimary: 300,
          windowMinutesSecondary: null,
        }),
      ],
      windows: {
        primary: {
          windowKey: "primary",
          windowMinutes: 300,
          accounts: [
            {
              accountId: "acc-1",
              remainingPercentAvg: 90,
              capacityCredits: 225,
              remainingCredits: 202.5,
            },
          ],
        },
        secondary: {
          windowKey: "secondary",
          windowMinutes: 10080,
          accounts: [
            {
              accountId: "acc-1",
              remainingPercentAvg: null,
              capacityCredits: 7560,
              remainingCredits: 0,
            },
          ],
        },
      },
      summary: {
        primaryWindow: {
          remainingPercent: 90,
          capacityCredits: 225,
          remainingCredits: 202.5,
          resetAt: null,
          windowMinutes: 300,
        },
        secondaryWindow: {
          remainingPercent: 0,
          capacityCredits: 7560,
          remainingCredits: 0,
          resetAt: null,
          windowMinutes: 10080,
        },
        cost: {
          currency: "USD",
          totalUsd: 1.82,
        },
        metrics: {
          requests: 228,
          tokens: 45000,
          cachedInputTokens: 8200,
          errorRate: 0.028,
          errorCount: 6,
          topError: "rate_limit_exceeded",
        },
      },
    });

    const view = buildDashboardView(overview, createDefaultRequestLogs(), false);

    expect(view.primaryUsageItems).toHaveLength(1);
    expect(view.primaryUsageItems[0]?.value).toBeCloseTo(202.5);
    expect(view.primaryUsageItems[0]?.remainingPercent).toBe(90);
    expect(overview.summary.primaryWindow.capacityCredits).toBe(225);
  });
});
