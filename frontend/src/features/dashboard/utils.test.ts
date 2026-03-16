import { describe, expect, it } from "vitest";

import {
  buildDashboardView,
  buildDepletionView,
  buildRemainingItems,
} from "@/features/dashboard/utils";
import type { AccountSummary, Depletion } from "@/features/dashboard/schemas";
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

describe("buildDashboardView", () => {
  it("adds plus-burn stat between cost and error rate", () => {
    const overview = createDashboardOverview();
    const logs = createDefaultRequestLogs();

    const view = buildDashboardView(overview, logs);

    expect(view.stats[2].label).toBe("Cost (7d)");
    expect(view.stats[3].label).toBe("Plus Burn (5h/7d)");
    expect(view.stats[3].value).toBe("0.7 / 1.2");
    expect(view.stats[3].meta).toBe("Primary 0.7 acc/5h · Secondary 1.2 acc/7d");
    expect(view.stats[3].trend.length).toBeGreaterThan(0);
    expect(view.stats[4].label).toBe("Error rate");
  });

  it("falls back to usage equivalents when depletion data is missing", () => {
    const overview = createDashboardOverview({
      depletionPrimary: null,
      depletionSecondary: null,
    });

    const view = buildDashboardView(overview, createDefaultRequestLogs());
    const burn = view.stats[3];

    expect(burn.label).toBe("Plus Burn (5h/7d)");
    expect(burn.value).toBe("0.7 / 1.2");
    expect(burn.meta).toBe("Primary 0.7 acc/5h · Secondary 1.2 acc/7d");
    expect(burn.trend.length).toBeGreaterThan(0);
  });

  it("uses usage-equivalent fallback when burn rate is zero", () => {
    const overview = createDashboardOverview({
      depletionSecondary: {
        risk: 1,
        riskLevel: "critical",
        burnRate: 0,
        safeUsagePercent: 98,
        projectedExhaustionAt: null,
        secondsUntilExhaustion: null,
      },
    });

    const view = buildDashboardView(overview, createDefaultRequestLogs());
    const burn = view.stats[3];

    expect(burn.value).toBe("0.7 / 1.2");
    expect(burn.meta).toBe("Primary 0.7 acc/5h · Secondary 1.2 acc/7d");
  });

  it("caps burn-equivalent to available account count per window", () => {
    const overview = createDashboardOverview({
      depletionSecondary: {
        risk: 1,
        riskLevel: "critical",
        burnRate: 999,
        safeUsagePercent: 98,
        projectedExhaustionAt: null,
        secondsUntilExhaustion: null,
      },
    });

    const view = buildDashboardView(overview, createDefaultRequestLogs());
    const burn = view.stats[3];

    expect(burn.value).toBe("0.7 / 2.0");
    expect(burn.meta).toBe("Primary 0.7 acc/5h · Secondary 2.0 acc/7d");
  });
});
