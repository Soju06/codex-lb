import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { AccountList } from "@/features/accounts/components/account-list";
import { DEFAULT_ACCOUNT_SORT_MODE, type AccountSortMode } from "@/features/accounts/sorting";
import { createAccountSummary } from "@/test/mocks/factories";

const accounts = [
  createAccountSummary({ accountId: "unknown", displayName: "Unknown monthly", status: "paused", availableResetCredits: 9, usage: { primaryRemainingPercent: 10, secondaryRemainingPercent: 10, monthlyRemainingPercent: null } }),
  createAccountSummary({ accountId: "zero", displayName: "Empty monthly", status: "active", availableResetCredits: 1, usage: { primaryRemainingPercent: 10, secondaryRemainingPercent: 10, monthlyRemainingPercent: 0 } }),
  createAccountSummary({ accountId: "high", displayName: "High monthly", status: "rate_limited", availableResetCredits: 2, usage: { primaryRemainingPercent: 10, secondaryRemainingPercent: 10, monthlyRemainingPercent: 80 } }),
];

function List() {
  const [sortMode, onSortModeChange] = useState<AccountSortMode>(DEFAULT_ACCOUNT_SORT_MODE);
  return <AccountList accounts={accounts} sortMode={sortMode} onSortModeChange={onSortModeChange} selectedAccountId={null} onSelect={vi.fn()} onOpenImport={vi.fn()} onOpenOauth={vi.fn()} />;
}

function visibleOrder() {
  return within(screen.getByTestId("account-list-scroll-region")).getAllByRole("button").map(button =>
    accounts.find(account => within(button).queryByText(account.displayName))?.accountId);
}

describe("Accounts sort selector", () => {
  it("applies monthly and status choices to rendered rows and retains the choice during filtering", async () => {
    const user = userEvent.setup();
    render(<List />);
    expect(visibleOrder()).toEqual(["unknown", "high", "zero"]);
    const sort = screen.getAllByRole("combobox")[1];
    await user.click(sort);
    await user.click(await screen.findByRole("option", { name: "Monthly quota (lowest remaining)" }));
    expect(visibleOrder()).toEqual(["zero", "high", "unknown"]);
    await user.type(screen.getByPlaceholderText("Search accounts..."), "monthly");
    expect(sort).toHaveTextContent("Monthly quota (lowest remaining)");
    expect(visibleOrder()).toEqual(["zero", "high", "unknown"]);
    await user.click(sort);
    await user.click(await screen.findByRole("option", { name: "Monthly quota (highest remaining)" }));
    expect(visibleOrder()).toEqual(["high", "zero", "unknown"]);
    await user.click(sort);
    await user.click(await screen.findByRole("option", { name: "Status (active first)" }));
    expect(visibleOrder()).toEqual(["zero", "unknown", "high"]);
  });
});
