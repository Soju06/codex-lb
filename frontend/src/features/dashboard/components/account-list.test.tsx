import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountList } from "@/features/dashboard/components/account-list";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { createAccountSummary } from "@/test/mocks/factories";

afterEach(() => {
  act(() => {
    usePrivacyStore.setState({ blurred: false });
  });
});

describe("AccountList", () => {
  function rowNames() {
    return screen.getAllByTestId("account-list-row").map((row) => {
      const paragraph = within(row).getAllByText(/Account$/)[0];
      return paragraph.textContent;
    });
  }

  it("renders a compact list with account status, quota, credits, and warm-up state", () => {
    render(
      <AccountList
        accounts={[
          createAccountSummary({
            accountId: "acc-1",
            displayName: "Primary Account",
            email: "primary@example.com",
            status: "active",
            creditsBalance: 42.5,
            limitWarmupEnabled: true,
          }),
        ]}
      />,
    );

    expect(screen.getByTestId("dashboard-account-list")).toBeInTheDocument();
    expect(screen.getByText("Primary Account")).toBeInTheDocument();
    expect(screen.getByText("primary@example.com")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("5h")).toBeInTheDocument();
    expect(screen.getByText("Weekly")).toBeInTheDocument();
    expect(screen.getAllByTestId("account-list-quota-meter")).toHaveLength(2);
    expect(screen.getByText("42.50")).toBeInTheDocument();
    expect(screen.getByText("On")).toBeInTheDocument();
  });

  it("renders primary idle warm-up attempts as 5h", () => {
    const attemptedAt = new Date("2026-06-03T12:00:00Z").toISOString();
    const account = createAccountSummary({
      accountId: "acc-idle",
      displayName: "Idle Warmup Account",
      limitWarmupEnabled: true,
      limitWarmup: {
        window: "primary_idle",
        resetAt: 18_000,
        status: "succeeded",
        model: "gpt-5.1-codex-mini",
        attemptedAt,
        completedAt: attemptedAt,
        errorCode: null,
        errorMessage: null,
      },
    });

    render(<AccountList accounts={[account]} />);

    const row = screen.getByTestId("account-list-row");
    expect(within(row).getByText((text) => text.includes("Succeeded | 5h |"))).toBeInTheDocument();
  });

  it("exposes account actions from list rows", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const account = createAccountSummary({
      accountId: "acc-paused",
      displayName: "Paused Account",
      status: "paused",
      limitWarmupEnabled: false,
      availableResetCredits: 1,
      resetCreditNearestExpiresAt: "2026-01-03T12:00:00.000Z",
    });

    render(<AccountList accounts={[account]} onAction={onAction} />);

    const resetButton = screen.getByRole("button", { name: "Redeem reset credit for Paused Account" });
    expect(resetButton).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "View details for Paused Account" }));
    await user.click(resetButton);
    await user.click(screen.getByRole("button", { name: "Enable limit warm-up for Paused Account" }));
    await user.click(screen.getByRole("button", { name: "Resume Paused Account" }));

    expect(onAction).toHaveBeenNthCalledWith(1, account, "details");
    expect(onAction).toHaveBeenNthCalledWith(2, account, "warmup-toggle");
    expect(onAction).toHaveBeenNthCalledWith(3, account, "resume");
    expect(onAction).not.toHaveBeenCalledWith(account, "reset-credit");
  });

  it("blurs list identity text when privacy mode is enabled", () => {
    act(() => {
      usePrivacyStore.setState({ blurred: true });
    });

    const { container } = render(
      <AccountList
        accounts={[
          createAccountSummary({
            accountId: "acc-private",
            displayName: "Private Account",
            email: "private@example.com",
          }),
        ]}
      />,
    );

    expect(screen.getByText("Private Account")).toBeInTheDocument();
    expect(container.querySelector(".privacy-blur")).not.toBeNull();
  });

  it("sorts by account header and toggles direction", async () => {
    const user = userEvent.setup();
    render(
      <AccountList
        accounts={[
          createAccountSummary({ accountId: "acc-b", displayName: "Beta Account" }),
          createAccountSummary({ accountId: "acc-a", displayName: "Alpha Account" }),
          createAccountSummary({ accountId: "acc-c", displayName: "Charlie Account" }),
        ]}
      />,
    );

    expect(rowNames()).toEqual(["Beta Account", "Alpha Account", "Charlie Account"]);

    await user.click(screen.getByRole("button", { name: "Account" }));

    expect(rowNames()).toEqual(["Alpha Account", "Beta Account", "Charlie Account"]);
    expect(screen.getByRole("button", { name: "Account, sorted ascending" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Account, sorted ascending" }));

    expect(rowNames()).toEqual(["Charlie Account", "Beta Account", "Alpha Account"]);
    expect(screen.getByRole("button", { name: "Account, sorted descending" })).toBeInTheDocument();
  });

  it("uses controlled sort state and reports the next header sort", async () => {
    const user = userEvent.setup();
    const onSortChange = vi.fn();
    render(
      <AccountList
        accounts={[
          createAccountSummary({ accountId: "acc-b", displayName: "Beta Account" }),
          createAccountSummary({ accountId: "acc-a", displayName: "Alpha Account" }),
        ]}
        sort={{ key: "account", direction: "asc" }}
        onSortChange={onSortChange}
      />,
    );

    expect(rowNames()).toEqual(["Alpha Account", "Beta Account"]);

    await user.click(screen.getByRole("button", { name: "Account, sorted ascending" }));

    expect(onSortChange).toHaveBeenCalledWith({ key: "account", direction: "desc" });
  });

  it("sorts quota by the lowest visible remaining quota percent", async () => {
    const user = userEvent.setup();
    render(
      <AccountList
        accounts={[
          createAccountSummary({
            accountId: "acc-healthy",
            displayName: "Healthy Account",
            usage: { primaryRemainingPercent: 91, secondaryRemainingPercent: 88 },
          }),
          createAccountSummary({
            accountId: "acc-low",
            displayName: "Low Account",
            usage: { primaryRemainingPercent: 62, secondaryRemainingPercent: 4 },
          }),
          createAccountSummary({
            accountId: "acc-mid",
            displayName: "Middle Account",
            usage: { primaryRemainingPercent: 50, secondaryRemainingPercent: 40 },
          }),
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Quota" }));

    expect(rowNames()).toEqual(["Low Account", "Middle Account", "Healthy Account"]);
  });

  it("sorts accounts with missing quota telemetry after real quota values", async () => {
    const user = userEvent.setup();
    render(
      <AccountList
        accounts={[
          createAccountSummary({
            accountId: "acc-unknown",
            displayName: "Unknown Account",
            usage: {
              primaryRemainingPercent: null,
              secondaryRemainingPercent: null,
              monthlyRemainingPercent: null,
            },
          }),
          createAccountSummary({
            accountId: "acc-empty",
            displayName: "Empty Account",
            usage: { primaryRemainingPercent: 0, secondaryRemainingPercent: 0 },
          }),
          createAccountSummary({
            accountId: "acc-low",
            displayName: "Low Account",
            usage: { primaryRemainingPercent: 18, secondaryRemainingPercent: 12 },
          }),
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Quota" }));

    expect(rowNames()).toEqual(["Empty Account", "Low Account", "Unknown Account"]);

    await user.click(screen.getByRole("button", { name: "Quota, sorted ascending" }));

    expect(rowNames()).toEqual(["Low Account", "Empty Account", "Unknown Account"]);
  });

  it("sorts accounts with missing credit telemetry after real credit balances", async () => {
    const user = userEvent.setup();
    render(
      <AccountList
        accounts={[
          createAccountSummary({
            accountId: "acc-unknown",
            displayName: "Unknown Account",
            creditsBalance: null,
            remainingCreditsPrimary: null,
            remainingCreditsSecondary: null,
            remainingCreditsMonthly: null,
          }),
          createAccountSummary({
            accountId: "acc-empty",
            displayName: "Empty Account",
            creditsBalance: 0,
          }),
          createAccountSummary({
            accountId: "acc-low",
            displayName: "Low Account",
            creditsBalance: 2.5,
          }),
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Credits" }));

    expect(rowNames()).toEqual(["Empty Account", "Low Account", "Unknown Account"]);

    await user.click(screen.getByRole("button", { name: "Credits, sorted ascending" }));

    expect(rowNames()).toEqual(["Low Account", "Empty Account", "Unknown Account"]);
  });

  it("hides the reset action when no reset credits are available", () => {
    render(
      <AccountList
        accounts={[
          createAccountSummary({
            accountId: "acc-no-reset",
            displayName: "No Reset Account",
            availableResetCredits: 0,
            resetCreditNearestExpiresAt: null,
          }),
        ]}
      />,
    );

    expect(
      screen.queryByRole("button", { name: "Redeem reset credit for No Reset Account" }),
    ).not.toBeInTheDocument();
  });

  it("shows the banked reset count as a bubble on the reset button", () => {
    render(
      <AccountList
        accounts={[
          createAccountSummary({
            accountId: "acc-reset",
            displayName: "Reset Account",
            availableResetCredits: 3,
          }),
        ]}
      />,
    );

    const resetButton = screen.getByRole("button", { name: "Redeem reset credit for Reset Account" });
    expect(within(resetButton).getByText("3")).toBeInTheDocument();
  });

  it("caps the reset count bubble at 99+", () => {
    render(
      <AccountList
        accounts={[
          createAccountSummary({
            accountId: "acc-many-reset",
            displayName: "Many Reset Account",
            availableResetCredits: 120,
          }),
        ]}
      />,
    );

    const resetButton = screen.getByRole("button", { name: "Redeem reset credit for Many Reset Account" });
    expect(within(resetButton).getByText("99+")).toBeInTheDocument();
  });
});
