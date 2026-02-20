import { describe, expect, it } from "vitest";

import { buildRemainingItems } from "@/features/dashboard/utils";
import type { AccountSummary } from "@/features/dashboard/schemas";
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
