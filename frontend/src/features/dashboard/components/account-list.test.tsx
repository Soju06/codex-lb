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

  it("exposes account actions from list rows", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const account = createAccountSummary({
      accountId: "acc-paused",
      displayName: "Paused Account",
      status: "paused",
      limitWarmupEnabled: false,
    });

    render(<AccountList accounts={[account]} onAction={onAction} />);

    await user.click(screen.getByRole("button", { name: "View details for Paused Account" }));
    await user.click(screen.getByRole("button", { name: "Enable limit warm-up for Paused Account" }));
    await user.click(screen.getByRole("button", { name: "Resume Paused Account" }));

    expect(onAction).toHaveBeenNthCalledWith(1, account, "details");
    expect(onAction).toHaveBeenNthCalledWith(2, account, "warmup-toggle");
    expect(onAction).toHaveBeenNthCalledWith(3, account, "resume");
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
});
