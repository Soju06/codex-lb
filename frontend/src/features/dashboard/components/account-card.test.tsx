import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AccountCard } from "@/features/dashboard/components/account-card";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountCard", () => {
  it("renders both primary and secondary quota bars for regular accounts", () => {
    const account = createAccountSummary();
    render(<AccountCard account={account} />);

    expect(screen.getByText("Primary")).toBeInTheDocument();
    expect(screen.getByText("Secondary")).toBeInTheDocument();
  });

  it("hides primary quota bar for weekly-only accounts", () => {
    const account = createAccountSummary({
      planType: "free",
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent: 76,
      },
      windowMinutesPrimary: null,
      windowMinutesSecondary: 10_080,
    });

    render(<AccountCard account={account} />);

    expect(screen.queryByText("Primary")).not.toBeInTheDocument();
    expect(screen.getByText("Secondary")).toBeInTheDocument();
  });

  it("renders tags inline with the email subtitle", () => {
    const account = createAccountSummary({
      displayName: "Primary Account",
      email: "primary@example.com",
      tags: ["paid", "team-a"],
    });

    render(<AccountCard account={account} />);

    expect(
      screen.getByText((_, element) => element?.textContent === "primary@example.com | Tags paid, team-a"),
    ).toBeInTheDocument();
    expect(screen.queryByText("paid")).not.toBeInTheDocument();
  });
});
