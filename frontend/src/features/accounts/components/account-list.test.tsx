import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountList } from "@/features/accounts/components/account-list";

describe("AccountList", () => {
  it("renders items and filters by search", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();

    render(
      <AccountList
        accounts={[
          {
            accountId: "acc-1",
            email: "primary@example.com",
            displayName: "Primary",
            planType: "plus",
            status: "active",
          },
          {
            accountId: "acc-2",
            email: "secondary@example.com",
            displayName: "Secondary",
            planType: "pro",
            status: "paused",
          },
        ]}
        selectedAccountId="acc-1"
        onSelect={onSelect}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    expect(screen.getByText("primary@example.com")).toBeInTheDocument();
    expect(screen.getByText("secondary@example.com")).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("Search accounts..."), "secondary");
    expect(screen.queryByText("primary@example.com")).not.toBeInTheDocument();
    expect(screen.getByText("secondary@example.com")).toBeInTheDocument();

    await user.click(screen.getByText("secondary@example.com"));
    expect(onSelect).toHaveBeenCalledWith("acc-2");
  });

  it("shows empty state when no items match filter", async () => {
    const user = userEvent.setup();

    render(
      <AccountList
        accounts={[
          {
            accountId: "acc-1",
            email: "primary@example.com",
            displayName: "Primary",
            planType: "plus",
            status: "active",
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    await user.type(screen.getByPlaceholderText("Search accounts..."), "not-found");
    expect(screen.getByText("No matching accounts")).toBeInTheDocument();
  });
});
