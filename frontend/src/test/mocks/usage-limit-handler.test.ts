import { describe, expect, it } from "vitest";

import { listAccounts, updateAccountUsageLimit } from "@/features/accounts/api";
import { createAccountSummary } from "@/test/mocks/factories";
import { resetMockState } from "@/test/mocks/handlers";

describe("default account usage-limit handler", () => {
  it("saves standalone window overrides and retains them when disabled", async () => {
    const saved = await updateAccountUsageLimit("acc_primary", {
      enabled: true,
      percent: null,
      percent5H: 70,
      percentWeekly: 60,
    });
    expect(saved).toMatchObject({
      enabled: true,
      percent: null,
      percent5H: 70,
      percentWeekly: 60,
    });

    const disabled = await updateAccountUsageLimit("acc_primary", { enabled: false });
    expect(disabled).toMatchObject({
      enabled: false,
      percent: null,
      percent5H: 70,
      percentWeekly: 60,
    });
    const accounts = await listAccounts();
    expect(accounts.accounts.find((account) => account.accountId === "acc_primary")).toMatchObject({
      usageLimitEnabled: false,
      usageLimit5HPercent: 70,
      usageLimitWeeklyPercent: 60,
    });

    const removed = await updateAccountUsageLimit("acc_primary", {
      enabled: false,
      percent: null,
      percent5H: null,
      percentWeekly: null,
    });
    expect(removed).toMatchObject({
      enabled: false,
      percent: null,
      percent5H: null,
      percentWeekly: null,
    });
  });

  it("updates effective window limits and evaluated state after a save", async () => {
    await updateAccountUsageLimit("acc_primary", {
      enabled: true, percent: 50, percentWeekly: 30,
    });
    let account = (await listAccounts()).accounts.find((item) => item.accountId === "acc_primary");
    expect(account).toMatchObject({
      effectiveLimitPrimary: 50,
      effectiveLimitSecondary: 30,
      usageLimitState: "reached",
    });

    await updateAccountUsageLimit("acc_primary", { enabled: true, percentWeekly: 40 });
    account = (await listAccounts()).accounts.find((item) => item.accountId === "acc_primary");
    expect(account).toMatchObject({
      effectiveLimitPrimary: 50,
      effectiveLimitSecondary: 40,
      usageLimitState: "available",
    });

    await updateAccountUsageLimit("acc_primary", { enabled: false });
    account = (await listAccounts()).accounts.find((item) => item.accountId === "acc_primary");
    expect(account).toMatchObject({
      effectiveLimitPrimary: null,
      effectiveLimitSecondary: null,
      usageLimitState: "disabled",
    });
  });

  it("fails closed when a limited window has no usage measurement", async () => {
    resetMockState([createAccountSummary({
      usage: { primaryRemainingPercent: null, secondaryRemainingPercent: 67 },
    })]);

    await updateAccountUsageLimit("acc_primary", { enabled: true, percent: 80 });
    const account = (await listAccounts()).accounts.find((item) => item.accountId === "acc_primary");
    expect(account).toMatchObject({ usageLimitState: "data_unavailable" });
  });

  it("ignores an unrestricted monthly window's saved weekly override", async () => {
    resetMockState([createAccountSummary({
      planType: "free",
      usage: { primaryRemainingPercent: null, secondaryRemainingPercent: null, monthlyRemainingPercent: 20 },
      windowMinutesPrimary: null,
      windowMinutesSecondary: null,
      windowMinutesMonthly: 43_200,
    })]);

    await updateAccountUsageLimit("acc_primary", { enabled: true, percentWeekly: 40 });
    const account = (await listAccounts()).accounts.find((item) => item.accountId === "acc_primary");
    expect(account).toMatchObject({
      effectiveLimitMonthly: null,
      usageLimitState: "available",
    });

    resetMockState([createAccountSummary({
      planType: "free",
      usage: { primaryRemainingPercent: null, secondaryRemainingPercent: null, monthlyRemainingPercent: null },
      windowMinutesPrimary: null,
      windowMinutesSecondary: null,
      windowMinutesMonthly: 43_200,
    })]);
    await updateAccountUsageLimit("acc_primary", { enabled: true, percentWeekly: 40 });
    const unrestricted = (await listAccounts()).accounts.find((item) => item.accountId === "acc_primary");
    expect(unrestricted).toMatchObject({ usageLimitState: "available" });

    await updateAccountUsageLimit("acc_primary", { enabled: true, percent: 40 });
    const restricted = (await listAccounts()).accounts.find((item) => item.accountId === "acc_primary");
    expect(restricted).toMatchObject({
      effectiveLimitMonthly: 40,
      usageLimitState: "data_unavailable",
    });
  });

  it.each([
    ["primary", 225, "data_unavailable"],
    ["primary", null, "data_unavailable"],
    ["primary", 0, "available"],
    ["secondary", 7_560, "data_unavailable"],
    ["secondary", null, "data_unavailable"],
  ] as const)("handles an absent %s override window with capacity %s", async (window, capacity, expected) => {
    resetMockState([createAccountSummary({
      capacityCreditsPrimary: window === "primary" ? capacity : 225,
      capacityCreditsSecondary: window === "secondary" ? capacity : 7_560,
      windowMinutesPrimary: window === "primary" ? null : 300,
      windowMinutesSecondary: window === "secondary" ? null : 10_080,
      usage: {
        primaryRemainingPercent: window === "primary" ? null : 82,
        secondaryRemainingPercent: window === "secondary" ? null : 67,
      },
    })]);
    await updateAccountUsageLimit("acc_primary", {
      enabled: true,
      percent5H: window === "primary" ? 40 : null,
      percentWeekly: window === "secondary" ? 40 : null,
    });
    const account = (await listAccounts()).accounts.find((item) => item.accountId === "acc_primary");
    expect(account).toMatchObject({ usageLimitState: expected });
  });
});
