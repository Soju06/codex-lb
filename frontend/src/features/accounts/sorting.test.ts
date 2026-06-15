import { describe, expect, it } from "vitest";

import {
  ACCOUNT_SORT_OPTIONS,
  sortAccountsForDisplay,
} from "@/features/accounts/sorting";
import { createAccountSummary } from "@/test/mocks/factories";

describe("sortAccountsForDisplay - available resets", () => {
  it("exposes an available resets sort option", () => {
    expect(
      ACCOUNT_SORT_OPTIONS.find((option) => option.value === "available_resets"),
    ).toEqual({ value: "available_resets", label: "Available resets" });
  });

  it("sorts accounts by available reset count descending (highest first)", () => {
    const accounts = [
      createAccountSummary({
        accountId: "a-none",
        displayName: "None Account",
        availableResetCount: 0,
      }),
      createAccountSummary({
        accountId: "b-high",
        displayName: "High Account",
        availableResetCount: 5,
      }),
      createAccountSummary({
        accountId: "c-low",
        displayName: "Low Account",
        availableResetCount: 2,
      }),
    ];

    const sorted = sortAccountsForDisplay(accounts, "both", "available_resets").map(
      (account) => account.accountId,
    );

    expect(sorted).toEqual(["b-high", "c-low", "a-none"]);
  });
});
