import { describe, expect, it } from "vitest";

import { resolveSelectedAccountId } from "@/features/accounts/selection";
import { createAccountSummary } from "@/test/mocks/factories";

describe("resolveSelectedAccountId", () => {
  it("keeps the sticky selection when account priority changes the list order", () => {
    const accounts = [
      createAccountSummary({
        accountId: "acc-top",
        email: "top@example.com",
        priority: "gold",
      }),
      createAccountSummary({
        accountId: "acc-sticky",
        email: "sticky@example.com",
        priority: "silver",
      }),
      createAccountSummary({
        accountId: "acc-bottom",
        email: "bottom@example.com",
        priority: "bronze",
      }),
    ];

    const reordered = [
      createAccountSummary({
        accountId: "acc-top",
        email: "top@example.com",
        priority: "bronze",
      }),
      createAccountSummary({
        accountId: "acc-sticky",
        email: "sticky@example.com",
        priority: "bronze",
      }),
      createAccountSummary({
        accountId: "acc-bottom",
        email: "bottom@example.com",
        priority: "gold",
      }),
    ];

    expect(resolveSelectedAccountId(accounts, "both", "acc-sticky")).toBe("acc-sticky");
    expect(resolveSelectedAccountId(reordered, "both", "acc-sticky")).toBe("acc-sticky");
  });

  it("falls back to the highest-priority account when there is no sticky selection", () => {
    const accounts = [
      createAccountSummary({
        accountId: "acc-low",
        email: "low@example.com",
        priority: "bronze",
      }),
      createAccountSummary({
        accountId: "acc-high",
        email: "high@example.com",
        priority: "gold",
      }),
    ];

    expect(resolveSelectedAccountId(accounts, "both", null)).toBe("acc-high");
  });

  it("uses an explicit selected account when present", () => {
    const accounts = [
      createAccountSummary({
        accountId: "acc-low",
        email: "low@example.com",
        priority: "bronze",
      }),
      createAccountSummary({
        accountId: "acc-high",
        email: "high@example.com",
        priority: "gold",
      }),
    ];

    expect(resolveSelectedAccountId(accounts, "both", "acc-low")).toBe("acc-low");
  });
});
