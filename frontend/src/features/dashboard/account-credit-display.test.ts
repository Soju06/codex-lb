import { describe, expect, it } from "vitest";

import {
  accountSubscriptionCredits,
  formatPurchasedCredits,
} from "@/features/dashboard/account-credit-display";
import { createAccountSummary } from "@/test/mocks/factories";

describe("account credit display", () => {
  it("uses monthly credits for monthly-only accounts", () => {
    const account = createAccountSummary({
      windowMinutesPrimary: null,
      windowMinutesSecondary: null,
      windowMinutesMonthly: 43_200,
      remainingCreditsPrimary: null,
      remainingCreditsSecondary: null,
      remainingCreditsMonthly: 900,
    });

    expect(accountSubscriptionCredits(account)).toBe(900);
  });

  it("uses secondary credits for weekly-only accounts", () => {
    const account = createAccountSummary({
      windowMinutesPrimary: null,
      windowMinutesSecondary: 10_080,
      windowMinutesMonthly: null,
      remainingCreditsPrimary: null,
      remainingCreditsSecondary: 700,
    });

    expect(accountSubscriptionCredits(account)).toBe(700);
  });

  it("prefers secondary credits when both subscription windows exist", () => {
    const account = createAccountSummary({
      windowMinutesPrimary: 300,
      windowMinutesSecondary: 10_080,
      remainingCreditsPrimary: 100,
      remainingCreditsSecondary: 600,
    });

    expect(accountSubscriptionCredits(account)).toBe(600);
  });

  it("falls back to primary credits when secondary credits are unavailable", () => {
    const account = createAccountSummary({
      windowMinutesPrimary: 300,
      windowMinutesSecondary: 10_080,
      remainingCreditsPrimary: 100,
      remainingCreditsSecondary: null,
    });

    expect(accountSubscriptionCredits(account)).toBe(100);
  });

  it("falls back to primary credits for legacy accounts without window metadata", () => {
    const account = createAccountSummary({
      windowMinutesPrimary: null,
      windowMinutesSecondary: null,
      windowMinutesMonthly: null,
      remainingCreditsPrimary: 100,
      remainingCreditsSecondary: null,
      remainingCreditsMonthly: null,
    });

    expect(accountSubscriptionCredits(account)).toBe(100);
  });

  it("formats unlimited only for purchased credits", () => {
    const account = createAccountSummary({ creditsUnlimited: true, creditsBalance: null });

    expect(formatPurchasedCredits(account, "Unlimited")).toBe("Unlimited");
    expect(accountSubscriptionCredits(account)).toBe(5_065.2);
  });
});
