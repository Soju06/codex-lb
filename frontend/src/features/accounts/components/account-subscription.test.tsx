import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AccountClockProvider,
  AccountSubscription,
} from "./account-subscription";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountSubscription", () => {
  afterEach(() => vi.useRealTimers());

  it("updates the remaining period and shows elapsed without changing account status", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-27T12:00:00Z"));
    const account = createAccountSummary({
      subscription: {
        activeUntil: "2026-09-27T12:02:00Z",
        lastCheckedAt: "2026-09-26T00:00:00Z",
      },
    });
    render(
      <AccountClockProvider>
        <AccountSubscription account={account} />
      </AccountClockProvider>,
    );
    expect(screen.getByText("2m remaining")).toBeInTheDocument();
    expect(screen.getByText("Recorded end date")).toBeInTheDocument();
    expect(screen.getByText("Last checked")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByText("1m remaining")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByText("Recorded period elapsed")).toBeInTheDocument();
    expect(account.status).toBe("active");
  });

  it.each([
    ["2026-10-15T20:00:00Z", "18d 08h"],
    ["2026-09-28T15:00:00Z", "01d 03h"],
    ["2026-09-27T12:59:00Z", "00d 00h"],
    ["2026-09-27T12:00:00Z", "Elapsed"],
    [null, "No data"],
  ])(
    "formats compact deadline %s without credential fallback",
    (activeUntil, label) => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-09-27T12:00:00Z"));
      render(
        <AccountSubscription
          compact
          account={createAccountSummary({
            subscription: {
              activeUntil,
              lastCheckedAt: "2026-09-26T00:00:00Z",
            },
            auth: { access: { expiresAt: "2030-01-01T00:00:00Z" } },
          })}
        />,
      );
      expect(screen.getByText(label)).toBeInTheDocument();
      expect(screen.getByTestId("account-plan-remaining")).toHaveAttribute(
        "title",
        expect.stringContaining("Last checked:"),
      );
      if (activeUntil)
        expect(screen.getByTestId("account-plan-remaining")).toHaveAttribute(
          "title",
          expect.stringContaining("Recorded end date:"),
        );
      expect(screen.queryByText(/remaining$/)).not.toBeInTheDocument();
    },
  );

  it("refreshes the compact label at hour and deadline boundaries", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-27T12:00:00Z"));
    const account = createAccountSummary({
      subscription: {
        activeUntil: "2026-09-27T13:00:00Z",
        lastCheckedAt: null,
      },
    });
    render(
      <AccountClockProvider>
        <AccountSubscription compact account={account} />
      </AccountClockProvider>,
    );
    expect(screen.getByText("00d 01h")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByText("00d 00h")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(59 * 60_000));
    expect(screen.getByText("Elapsed")).toBeInTheDocument();
    expect(screen.getByTestId("account-plan-remaining")).toHaveAttribute(
      "aria-label",
      expect.stringContaining("Recorded period elapsed"),
    );
    expect(account.status).toBe("active");
  });

  it("does not use access-token expiry as a subscription deadline", () => {
    render(
      <AccountSubscription
        account={createAccountSummary({
          subscription: { activeUntil: null, lastCheckedAt: null },
          auth: { access: { expiresAt: "2030-01-01T00:00:00Z" } },
        })}
      />,
    );
    expect(screen.getByText("No data")).toBeInTheDocument();
    expect(screen.queryByText("Recorded end date")).not.toBeInTheDocument();
  });
});
