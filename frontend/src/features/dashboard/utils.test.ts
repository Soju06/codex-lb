import { describe, expect, it } from "vitest";

import {
  buildAdditionalQuotaItems,
  buildDepletionView,
  buildRemainingItems,
  formatLimitName,
} from "@/features/dashboard/utils";
import type { AccountSummary, AdditionalQuota, Depletion } from "@/features/dashboard/schemas";
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
  };
}

describe("formatLimitName", () => {
  it("maps codex_other to Codex Spark", () => {
    expect(formatLimitName("codex_other")).toBe("Codex Spark");
  });

  it("passes through unknown limit names", () => {
    expect(formatLimitName("unknown_limit")).toBe("unknown_limit");
  });
});

describe("buildAdditionalQuotaItems", () => {
  it("returns empty array for empty quotas", () => {
    const items = buildAdditionalQuotaItems([]);
    expect(items).toEqual([]);
  });

  it("maps quota with primaryWindow correctly", () => {
    const quotas: AdditionalQuota[] = [
      {
        limitName: "codex_other",
        meteredFeature: "spark_requests",
        primaryWindow: {
          usedPercent: 45,
          resetAt: 1234567890,
          windowMinutes: 60,
        },
        secondaryWindow: null,
      },
    ];

    const items = buildAdditionalQuotaItems(quotas);
    expect(items).toHaveLength(1);
    expect(items[0]).toEqual({
      limitName: "codex_other",
      displayName: "Codex Spark",
      primaryUsedPercent: 45,
      primaryResetAt: 1234567890,
      primaryWindowMinutes: 60,
      secondaryUsedPercent: null,
      secondaryResetAt: null,
      secondaryWindowMinutes: null,
    });
  });

  it("handles null windows correctly", () => {
    const quotas: AdditionalQuota[] = [
      {
        limitName: "codex_other",
        meteredFeature: "spark_requests",
        primaryWindow: null,
        secondaryWindow: null,
      },
    ];

    const items = buildAdditionalQuotaItems(quotas);
    expect(items[0]).toEqual({
      limitName: "codex_other",
      displayName: "Codex Spark",
      primaryUsedPercent: null,
      primaryResetAt: null,
      primaryWindowMinutes: null,
      secondaryUsedPercent: null,
      secondaryResetAt: null,
      secondaryWindowMinutes: null,
    });
  });

  it("maps both primary and secondary windows", () => {
    const quotas: AdditionalQuota[] = [
      {
        limitName: "codex_other",
        meteredFeature: "spark_requests",
        primaryWindow: {
          usedPercent: 30,
          resetAt: 1000,
          windowMinutes: 60,
        },
        secondaryWindow: {
          usedPercent: 70,
          resetAt: 2000,
          windowMinutes: 1440,
        },
      },
    ];

    const items = buildAdditionalQuotaItems(quotas);
    expect(items[0]).toEqual({
      limitName: "codex_other",
      displayName: "Codex Spark",
      primaryUsedPercent: 30,
      primaryResetAt: 1000,
      primaryWindowMinutes: 60,
      secondaryUsedPercent: 70,
      secondaryResetAt: 2000,
      secondaryWindowMinutes: 1440,
    });
  });
});

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
      window: "primary",
    };
    expect(buildDepletionView(depletion)).toBeNull();
  });

  it("returns view for warning risk level", () => {
    const depletion: Depletion = {
      risk: 0.5,
      riskLevel: "warning",
      burnRate: 1.5,
      safeUsagePercent: 45,
      window: "primary",
    };
    const view = buildDepletionView(depletion);
    expect(view).toEqual({
      safePercent: 45,
      riskLevel: "warning",
      window: "primary",
    });
  });

  it("returns view for danger risk level", () => {
    const depletion: Depletion = {
      risk: 0.75,
      riskLevel: "danger",
      burnRate: 2.5,
      safeUsagePercent: 30,
      window: "primary",
    };
    const view = buildDepletionView(depletion);
    expect(view).toEqual({
      safePercent: 30,
      riskLevel: "danger",
      window: "primary",
    });
  });

  it("returns view for critical risk level", () => {
    const depletion: Depletion = {
      risk: 0.95,
      riskLevel: "critical",
      burnRate: 5.0,
      safeUsagePercent: 20,
      window: "primary",
    };
    const view = buildDepletionView(depletion);
    expect(view).toEqual({
      safePercent: 20,
      riskLevel: "critical",
      window: "primary",
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

    expect(items[0].label).toBe(`dup@example.com (${formatCompactAccountId(duplicateA, 5, 4)})`);
    expect(items[1].label).toBe(`dup@example.com (${formatCompactAccountId(duplicateB, 5, 4)})`);
    expect(items[2].label).toBe("unique@example.com");
  });
});
