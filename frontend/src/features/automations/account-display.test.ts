import { describe, expect, it } from "vitest";

import { createAccountSummary } from "@/test/mocks/factories";
import {
  buildAccountDisplayIndex,
  formatAccountsSummary,
  resolveAccountDisplay,
} from "@/features/automations/account-display";

describe("automations account display", () => {
  it("renders human-readable account names from account index", () => {
    const accounts = [
      createAccountSummary({
        accountId: "acc_1",
        displayName: "Primary workspace",
        email: "primary@example.com",
      }),
    ];
    const index = buildAccountDisplayIndex(accounts);

    const resolved = resolveAccountDisplay("acc_1", index);
    expect(resolved.primary).toBe("Primary workspace");
    expect(resolved.secondary).toBe("primary@example.com");
  });

  it("uses unknown-account fallback when account id is missing in index", () => {
    const index = buildAccountDisplayIndex([]);
    const resolved = resolveAccountDisplay("acc_missing_123456789", index);

    expect(resolved.primary).toBe("Unknown account");
    expect(resolved.secondary).toContain("ID ");
    expect(resolved.title).toContain("acc_missing_123456789");
  });

  it("renders all-accounts summary for empty job account list", () => {
    const index = buildAccountDisplayIndex([]);
    const summary = formatAccountsSummary([], index);

    expect(summary.primary).toBe("All accounts");
    expect(summary.secondary).toBe("Uses every available account");
  });

  it("renders compact multi-account summary for large account list", () => {
    const accounts = [
      createAccountSummary({ accountId: "acc_1", displayName: "Alpha", email: "alpha@example.com" }),
      createAccountSummary({ accountId: "acc_2", displayName: "Beta", email: "beta@example.com" }),
      createAccountSummary({ accountId: "acc_3", displayName: "Gamma", email: "gamma@example.com" }),
      createAccountSummary({ accountId: "acc_4", displayName: "Delta", email: "delta@example.com" }),
    ];
    const index = buildAccountDisplayIndex(accounts);

    const summary = formatAccountsSummary(["acc_1", "acc_2", "acc_3", "acc_4"], index);
    expect(summary.primary).toBe("4 accounts");
    expect(summary.secondary).toContain("Alpha");
    expect(summary.secondary).toContain("+2 more");
  });
});
