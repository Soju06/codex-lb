import { render, screen } from "@testing-library/react";
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

  it("renders mapped label for the known gated additional quota limit", () => {
    const account = createAccountSummary({
      additionalQuotas: [
        {
          limitName: "codex_spark",
          meteredFeature: "codex_bengalfox",
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
});
