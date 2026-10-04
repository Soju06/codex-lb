import { describe, expect, it } from "vitest";

import { DEFAULT_ACCOUNT_SORT_MODE, sortAccountsForDisplay, type AccountSortMode } from "@/features/accounts/sorting";
import type { AccountSummary } from "@/features/accounts/schemas";
import { createAccountSummary } from "@/test/mocks/factories";

const BOTH = "both";

describe("sortAccountsForDisplay — most_reset_credits", () => {
  it("uses most reset credits as the default sort mode", () => {
    expect(DEFAULT_ACCOUNT_SORT_MODE).toBe("most_reset_credits");
  });

  it("orders accounts by available reset credits descending", () => {
    const fewer = createAccountSummary({
      accountId: "acc-fewer",
      displayName: "Fewer",
      availableResetCredits: 1,
    });
    const more = createAccountSummary({
      accountId: "acc-more",
      displayName: "More",
      availableResetCredits: 4,
    });

    const sorted = sortAccountsForDisplay([fewer, more], BOTH, "most_reset_credits");

    expect(sorted.map((account) => account.accountId)).toEqual([
      "acc-more",
      "acc-fewer",
    ]);
  });

  it("breaks ties by soonest expiry ascending", () => {
    const later = createAccountSummary({
      accountId: "acc-later",
      displayName: "Later",
      availableResetCredits: 2,
      resetCreditNearestExpiresAt: "2026-02-01T00:00:00.000Z",
    });
    const sooner = createAccountSummary({
      accountId: "acc-sooner",
      displayName: "Sooner",
      availableResetCredits: 2,
      resetCreditNearestExpiresAt: "2026-01-10T00:00:00.000Z",
    });

    const sorted = sortAccountsForDisplay([later, sooner], BOTH, "most_reset_credits");

    expect(sorted.map((account) => account.accountId)).toEqual([
      "acc-sooner",
      "acc-later",
    ]);
  });

  it("sorts accounts with null expiry after accounts that have one", () => {
    const noExpiry = createAccountSummary({
      accountId: "acc-no-expiry",
      displayName: "No Expiry",
      availableResetCredits: 3,
      resetCreditNearestExpiresAt: null,
    });
    const withExpiry = createAccountSummary({
      accountId: "acc-with-expiry",
      displayName: "With Expiry",
      availableResetCredits: 3,
      resetCreditNearestExpiresAt: "2026-01-10T00:00:00.000Z",
    });

    const sorted = sortAccountsForDisplay(
      [noExpiry, withExpiry],
      BOTH,
      "most_reset_credits",
    ) as AccountSummary[];

    expect(sorted.map((account) => account.accountId)).toEqual([
      "acc-with-expiry",
      "acc-no-expiry",
    ]);
  });

  it("still sorts credits descending when both accounts have null expiry", () => {
    const low = createAccountSummary({
      accountId: "acc-low",
      displayName: "Low",
      availableResetCredits: 1,
      resetCreditNearestExpiresAt: null,
    });
    const high = createAccountSummary({
      accountId: "acc-high",
      displayName: "High",
      availableResetCredits: 5,
      resetCreditNearestExpiresAt: null,
    });

    const sorted = sortAccountsForDisplay([low, high], BOTH, "most_reset_credits");

    expect(sorted.map((account) => account.accountId)).toEqual([
      "acc-high",
      "acc-low",
    ]);
  });
});


describe("status and remaining quota sorting", () => {
  it.each([
    ["quota_5h", "primaryRemainingPercent"],
    ["quota_7d", "secondaryRemainingPercent"],
    ["quota_monthly", "monthlyRemainingPercent"],
  ] as const)("sorts %s in both directions with zero known and unknown last", (mode, field) => {
    const accounts = [
      createAccountSummary({ accountId: "unknown", usage: { primaryRemainingPercent: null, secondaryRemainingPercent: null, [field]: null } }),
      createAccountSummary({ accountId: "zero", usage: { primaryRemainingPercent: null, secondaryRemainingPercent: null, [field]: 0 } }),
      createAccountSummary({ accountId: "high", usage: { primaryRemainingPercent: null, secondaryRemainingPercent: null, [field]: 80 } }),
    ];
    expect(sortAccountsForDisplay(accounts, BOTH, `${mode}_asc` as AccountSortMode).map(a => a.accountId)).toEqual(["zero", "high", "unknown"]);
    expect(sortAccountsForDisplay(accounts, BOTH, `${mode}_desc` as AccountSortMode).map(a => a.accountId)).toEqual(["high", "zero", "unknown"]);
    expect(accounts.map(a => a.accountId)).toEqual(["unknown", "zero", "high"]);
  });

  it("orders all statuses and reverses their priority", () => {
    const statuses = ["active", "paused", "rate_limited", "quota_exceeded", "reauth_required", "deactivated"] as const;
    const accounts = statuses.map(status => createAccountSummary({ accountId: status, status })).reverse();
    expect(sortAccountsForDisplay(accounts, BOTH, "status_asc").map(a => a.status)).toEqual(statuses);
    expect(sortAccountsForDisplay(accounts, BOTH, "status_desc").map(a => a.status)).toEqual([...statuses].reverse());
  });

  it("uses existing name and ID tie breakers for equal and missing quotas", () => {
    const accounts = [
      createAccountSummary({ accountId: "z", displayName: "Beta", resetAtPrimary: null, resetAtSecondary: null, usage: null }),
      createAccountSummary({ accountId: "b", displayName: "Alpha", resetAtPrimary: null, resetAtSecondary: null, usage: null }),
      createAccountSummary({ accountId: "a", displayName: "Alpha", resetAtPrimary: null, resetAtSecondary: null, usage: null }),
    ];
    expect(sortAccountsForDisplay(accounts, BOTH, "quota_monthly_desc").map(a => a.accountId)).toEqual(["a", "b", "z"]);
  });
});
