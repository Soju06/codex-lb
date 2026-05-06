import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountListItem } from "@/features/accounts/components/account-list-item";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountListItem", () => {
  beforeEach(() => {
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "both" });
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T12:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders neutral quota track when secondary remaining percent is unknown", () => {
    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: 82,
        secondaryRemainingPercent: null,
      },
    });

    render(<AccountListItem account={account} selected={false} onSelect={vi.fn()} />);

    expect(screen.getByTestId("mini-quota-track-weekly")).toHaveClass("bg-muted");
    expect(screen.queryByTestId("mini-quota-track-weekly-fill")).not.toBeInTheDocument();
    expect(screen.getByText("5h")).toBeInTheDocument();
    expect(screen.getByText("Weekly")).toBeInTheDocument();
    expect(screen.getByText("Reset in 1h")).toBeInTheDocument();
    expect(screen.getByText("Reset in 1d")).toBeInTheDocument();
  });

  it("omits the 5h row for weekly-only accounts", () => {
    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent: 73,
      },
      resetAtPrimary: null,
      resetAtSecondary: "2026-01-02T12:00:00.000Z",
      windowMinutesPrimary: null,
      windowMinutesSecondary: 10_080,
    });

    render(<AccountListItem account={account} selected={false} onSelect={vi.fn()} />);

    expect(screen.queryByText("5h")).not.toBeInTheDocument();
    expect(screen.getByText("Weekly")).toBeInTheDocument();
    expect(screen.getByText("Reset in 1d")).toBeInTheDocument();
  });

  it("renders legacy primary quota data without window metadata", () => {
    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: 64,
        secondaryRemainingPercent: null,
      },
      resetAtPrimary: "2026-01-01T13:00:00.000Z",
      resetAtSecondary: null,
      windowMinutesPrimary: null,
      windowMinutesSecondary: null,
    });

    render(<AccountListItem account={account} selected={false} onSelect={vi.fn()} />);

    expect(screen.getByText("5h")).toBeInTheDocument();
    expect(screen.getByTestId("mini-quota-track-5h-fill")).toHaveStyle({ width: "64%" });
    expect(screen.getByText("Reset in 1h")).toBeInTheDocument();
    expect(screen.queryByText("Weekly")).not.toBeInTheDocument();
  });

  it("does not duplicate unavailable reset labels", () => {
    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: 64,
        secondaryRemainingPercent: null,
      },
      resetAtPrimary: null,
      resetAtSecondary: null,
      windowMinutesPrimary: 300,
      windowMinutesSecondary: null,
    });

    render(<AccountListItem account={account} selected={false} onSelect={vi.fn()} />);

    expect(screen.getByText("Reset --")).toBeInTheDocument();
    expect(screen.queryByText("Reset Reset unavailable")).not.toBeInTheDocument();
  });

  it("shows only the 5h row when the account quota preference is 5h", () => {
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "5h" });

    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: 82,
        secondaryRemainingPercent: 73,
      },
    });

    render(<AccountListItem account={account} selected={false} onSelect={vi.fn()} />);

    expect(screen.getByText("5h")).toBeInTheDocument();
    expect(screen.queryByText("Weekly")).not.toBeInTheDocument();
  });

  it("renders quota fill when secondary remaining percent is available", () => {
    const account = createAccountSummary({
      usage: {
        primaryRemainingPercent: 82,
        secondaryRemainingPercent: 73,
      },
    });

    render(<AccountListItem account={account} selected={false} onSelect={vi.fn()} />);

    expect(screen.getByTestId("mini-quota-track-weekly-fill")).toHaveStyle({ width: "73%" });
  });

  it("marks burn-first accounts in the list", () => {
    const account = createAccountSummary({ routingPolicy: "burn_first" });

    render(<AccountListItem account={account} selected={false} onSelect={vi.fn()} />);

    expect(screen.getByLabelText("Burn first")).toBeInTheDocument();
  });

  it("marks preserved accounts in the list", () => {
    const account = createAccountSummary({ routingPolicy: "preserve" });

    render(<AccountListItem account={account} selected={false} onSelect={vi.fn()} />);

    expect(screen.getByLabelText("Preserve")).toBeInTheDocument();
  });
});
