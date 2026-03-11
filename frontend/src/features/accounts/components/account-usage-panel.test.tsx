import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AccountUsagePanel } from "@/features/accounts/components/account-usage-panel";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountUsagePanel", () => {
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

    expect(screen.getByText("Primary remaining")).toBeInTheDocument();
    expect(screen.getByText("--")).toBeInTheDocument();
  });

  it("hides primary row for weekly-only accounts", () => {
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

    expect(screen.queryByText("Primary remaining")).not.toBeInTheDocument();
    expect(screen.getByText("Secondary remaining")).toBeInTheDocument();
  });

  it("renders mapped label for the known gated additional quota limit", () => {
    const account = createAccountSummary({
      additionalQuotas: [
        {
          limitName: "codex_other",
          meteredFeature: "codex_bengalfox",
          primaryWindow: {
            usedPercent: 35,
            resetAt: 1_762_400_000,
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
});
