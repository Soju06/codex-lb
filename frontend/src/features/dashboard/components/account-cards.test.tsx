import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountCards } from "@/features/dashboard/components/account-cards";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountCards", () => {
  it("caps the dashboard account grid at three visible rows without clipping taller cards", () => {
    render(
      <AccountCards
        accounts={Array.from({ length: 7 }, (_, index) =>
          createAccountSummary({
            accountId: `acc-${index + 1}`,
            email: `account-${index + 1}@example.com`,
            displayName: `Account ${index + 1}`,
          }),
        )}
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByTestId("dashboard-account-cards")).toHaveStyle({
      maxHeight: "calc(3 * 11.5rem + 2rem)",
    });
  });

  it("keeps the scrollbar hidden on the dashboard account grid", () => {
    render(
      <AccountCards
        accounts={[createAccountSummary(), createAccountSummary({ accountId: "acc-2", email: "two@example.com" })]}
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByTestId("dashboard-account-cards")).toHaveClass(
      "overflow-y-auto",
      "[scrollbar-width:none]",
      "[&::-webkit-scrollbar]:hidden",
    );
  });

  it("hides deactivated accounts by default and allows choosing multiple statuses", async () => {
    const user = userEvent.setup();
    render(
      <AccountCards
        accounts={[
          createAccountSummary({
            accountId: "acc-active",
            email: "active@example.com",
            displayName: "active@example.com",
            status: "active",
          }),
          createAccountSummary({
            accountId: "acc-paused",
            email: "paused@example.com",
            displayName: "paused@example.com",
            status: "paused",
          }),
          createAccountSummary({
            accountId: "acc-deactivated",
            email: "inactive@example.com",
            displayName: "inactive@example.com",
            status: "deactivated",
          }),
        ]}
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByText("active@example.com")).toBeInTheDocument();
    expect(screen.getByText("paused@example.com")).toBeInTheDocument();
    expect(screen.queryByText("inactive@example.com")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /statuses/i }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: /deactivated/i }));

    expect(screen.getByText("active@example.com")).toBeInTheDocument();
    expect(screen.getByText("paused@example.com")).toBeInTheDocument();
    expect(screen.getByText("inactive@example.com")).toBeInTheDocument();
  });

  it("gives each warm-up toggle a descriptive account-specific name", () => {
    render(
      <AccountCards
        accounts={[
          createAccountSummary({
            accountId: "acc-1",
            email: "one@example.com",
            displayName: "One Account",
            limitWarmupEnabled: false,
          }),
          createAccountSummary({
            accountId: "acc-2",
            email: "two@example.com",
            displayName: "Two Account",
            limitWarmupEnabled: true,
          }),
        ]}
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Enable limit warm-up for One Account" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disable limit warm-up for Two Account" })).toBeInTheDocument();
  });

  it("shows account ids only for backend-marked duplicate account slots", () => {
    render(
      <AccountCards
        accounts={[
          createAccountSummary({
            accountId: "d48f0bfc-8ea6-48a7-8d76-d0e5ef1816c5_6f12b5d5",
            email: "dup@example.com",
            displayName: "Same email, different workspace",
            isEmailDuplicate: false,
          }),
          createAccountSummary({
            accountId: "7f9de2ad-7621-4a6f-88bc-ec7f3d914701_91a95cee",
            email: "dup@example.com",
            displayName: "Same email, duplicate slot",
            isEmailDuplicate: true,
          }),
        ]}
        onAction={vi.fn()}
      />,
    );

    expect(screen.queryByText((_content, el) => el?.tagName === "P" && !!el.textContent?.match(/dup@example\.com .* ID d48f0bfc\.\.\.12b5d5/))).not.toBeInTheDocument();
    expect(screen.getByText((_content, el) => el?.tagName === "P" && !!el.textContent?.match(/dup@example\.com .* ID 7f9de2ad\.\.\.a95cee/))).toBeInTheDocument();
  });
});
