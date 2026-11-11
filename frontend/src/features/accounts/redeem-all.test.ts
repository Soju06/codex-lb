import { describe, expect, it } from "vitest";

import { getRedeemAllTargets } from "@/features/accounts/redeem-all";
import { createAccountSummary } from "@/test/mocks/factories";

describe("getRedeemAllTargets", () => {
  it("keeps only accounts with banked credits that can redeem them", () => {
    const accounts = [
      createAccountSummary({ accountId: "a", email: "a@example.com", displayName: "Alpha", availableResetCredits: 2 }),
      createAccountSummary({ accountId: "b", email: "b@example.com", displayName: "b@example.com", availableResetCredits: 1, status: "rate_limited" }),
      createAccountSummary({ accountId: "c", availableResetCredits: 0 }),
      createAccountSummary({ accountId: "d", availableResetCredits: null }),
      createAccountSummary({ accountId: "e", availableResetCredits: 3, status: "paused" }),
      createAccountSummary({ accountId: "f", availableResetCredits: 3, status: "reauth_required" }),
      createAccountSummary({ accountId: "g", availableResetCredits: 3, status: "deactivated" }),
    ];

    expect(getRedeemAllTargets(accounts)).toEqual([
      { accountId: "a", label: "Alpha", email: "a@example.com", availableResetCredits: 2 },
      { accountId: "b", label: "b@example.com", email: "b@example.com", availableResetCredits: 1 },
    ]);
  });

  it("returns nothing when no account holds a credit", () => {
    expect(getRedeemAllTargets([createAccountSummary({ availableResetCredits: 0 })])).toEqual([]);
  });
});
