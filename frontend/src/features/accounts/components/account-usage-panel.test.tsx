import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountUsagePanel } from "@/features/accounts/components/account-usage-panel";
import { createAccountSummary, createAccountTrends } from "@/test/mocks/factories";

describe("AccountUsagePanel", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows '--' for missing quota percent instead of 0%", () => {
    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent: 67,
      },
      windowMinutesPrimary: 300,
      windowMinutesSecondary: 10_080,
    });

    render(<AccountUsagePanel account={account} trends={null} />);

    expect(screen.getByText("5h remaining")).toBeInTheDocument();
    expect(screen.getByText("--")).toBeInTheDocument();
  });

  it("hides 5h row for weekly-only accounts", () => {
    const account = createAccountSummary({
      planType: "free",
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent: 76,
      },
      windowMinutesPrimary: null,
      windowMinutesSecondary: 10_080,
    });

    render(<AccountUsagePanel account={account} trends={null} />);

    expect(screen.queryByText("5h remaining")).not.toBeInTheDocument();
    expect(screen.getByText("Weekly remaining")).toBeInTheDocument();
  });

  it("shows only Monthly for monthly-only free accounts", () => {
    const account = createAccountSummary({
      planType: "free",
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent: null,
        monthlyRemainingPercent: 95,
      },
      windowMinutesPrimary: null,
      windowMinutesSecondary: null,
      windowMinutesMonthly: 43_200,
      resetAtPrimary: null,
      resetAtSecondary: null,
      resetAtMonthly: "2026-01-31T00:00:00.000Z",
    });

    render(<AccountUsagePanel account={account} trends={null} />);

    expect(screen.getByText("Monthly remaining")).toBeInTheDocument();
    expect(screen.queryByText("5h remaining")).not.toBeInTheDocument();
    expect(screen.queryByText("Weekly remaining")).not.toBeInTheDocument();
  });

  it("renders mapped label for the known gated additional quota limit", () => {
    const account = createAccountSummary({
      additionalQuotas: [
        {
          limitName: "codex_spark",
          meteredFeature: "codex_bengalfox",
          routingPolicy: "inherit",
          primaryWindow: {
            usedPercent: 35,
            resetAt: Math.floor(new Date("2026-01-07T13:00:00.000Z").getTime() / 1000),
            windowMinutes: 300,
          },
          secondaryWindow: null,
        },
      ],
    });

    render(<AccountUsagePanel account={account} trends={null} />);

    expect(screen.getByText("Additional Quotas")).toBeInTheDocument();
    expect(screen.getByText("GPT-5.3-Codex-Spark")).toBeInTheDocument();
    expect(screen.getByText(/35% used/)).toBeInTheDocument();
    expect(screen.getByText("Resets in 6d 13h")).toBeInTheDocument();
  });

  it("renders request log usage summary when available", () => {
    const account = createAccountSummary({
      requestUsage: {
        requestCount: 7,
        totalTokens: 51_480,
        cachedInputTokens: 41_470,
        totalCostUsd: 0.13,
      },
    });

    render(<AccountUsagePanel account={account} trends={null} />);

    expect(screen.getByText("Request logs total")).toBeInTheDocument();
    expect(screen.getByText(/\$0\.13/)).toBeInTheDocument();
    expect(screen.getByText(/51\.48K tok/)).toBeInTheDocument();
  });

  it("renders usage reset credit availability when provided", () => {
    const account = createAccountSummary();

    render(
      <AccountUsagePanel
        account={account}
        trends={null}
        resetCredits={{ availableCount: 3 }}
      />,
    );

    expect(screen.getByText("Usage resets")).toBeInTheDocument();
    expect(screen.getByText("3 available")).toBeInTheDocument();
  });

  it("shows the nearest reset-credit expiry when provided", () => {
    const account = createAccountSummary({
      resetCreditNearestExpiresAt: "2026-01-01T02:00:00.000Z",
    });

    render(
      <AccountUsagePanel
        account={account}
        trends={null}
        resetCredits={{ availableCount: 3 }}
      />,
    );

    expect(
      screen.getByText((_, element) => {
        const text = element?.textContent ?? "";
        return text.startsWith("Expires ") && text.includes("(2h)") && !text.includes("available");
      }),
    ).toBeInTheDocument();
    const row = screen.getByText("Usage resets").closest("div");
    const rowText = row?.textContent ?? "";
    expect(rowText.indexOf("Expires ")).toBeGreaterThan(-1);
    expect(rowText.indexOf("Expires ")).toBeLessThan(rowText.indexOf("3 available"));
  });

  it("renders a usage reset action when provided", () => {
    const account = createAccountSummary({ accountId: "acc_reset" });
    const onReset = vi.fn();

    render(
      <AccountUsagePanel
        account={account}
        trends={null}
        resetCredits={{ availableCount: 1 }}
        onReset={onReset}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Reset usage" }));

    expect(onReset).toHaveBeenCalledWith("acc_reset");
    expect(onReset).toHaveBeenCalledTimes(1);
  });

  it("shows the weekly plan legend when scheduled trend data exists", () => {
    const account = createAccountSummary();
    const trends = createAccountTrends(account.accountId, {
      secondaryScheduled: [
        { t: "2026-01-01T00:00:00.000Z", v: 100 },
        { t: "2026-01-01T01:00:00.000Z", v: 99.4 },
      ],
    });

    render(<AccountUsagePanel account={account} trends={trends} />);

    expect(screen.getByText("Weekly plan")).toBeInTheDocument();
  });

  it("shows trends when only secondary scheduled trend points are present", () => {
    const account = createAccountSummary();
    const trends = createAccountTrends(account.accountId, {
      primary: [],
      secondary: [],
      secondaryScheduled: [
        { t: "2026-01-01T00:00:00.000Z", v: 100 },
        { t: "2026-01-01T06:00:00.000Z", v: 92 },
      ],
    });

    render(<AccountUsagePanel account={account} trends={trends} />);

    expect(screen.getByText("7-day trend")).toBeInTheDocument();
    expect(screen.getByText("Weekly plan")).toBeInTheDocument();
  });
});
