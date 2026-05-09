import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountCards } from "@/features/dashboard/components/account-cards";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountCards", () => {
  it("caps the dashboard account grid at two visible rows without clipping taller cards", () => {
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
      maxHeight: "calc(2 * 13.5rem + 1rem)",
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

  it("passes the priorities toggle through to dashboard cards", () => {
    render(
      <AccountCards
        accounts={[createAccountSummary({ priority: "gold" })]}
        showPriorities={false}
        onAction={vi.fn()}
      />,
    );

    expect(screen.queryByText("Gold")).not.toBeInTheDocument();
  });
});
