import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AccountList } from "./account-list";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { createAccountSummary } from "@/test/mocks/factories";

const accounts = [
  createAccountSummary({
    accountId: "c",
    displayName: "Cedar",
    planType: "unknown",
    subscription: null,
    auth: { access: { expiresAt: "2030-01-01T00:00:00Z" } },
    usage: {
      primaryRemainingPercent: null,
      secondaryRemainingPercent: null,
      monthlyRemainingPercent: 98,
    },
  }),
  createAccountSummary({
    accountId: "a",
    displayName: "Atlas",
    planType: "PRO",
    subscription: { activeUntil: "2026-11-01T00:00:00Z", lastCheckedAt: null },
    usage: { primaryRemainingPercent: 90, secondaryRemainingPercent: 0 },
  }),
  createAccountSummary({
    accountId: "e",
    displayName: "Echo",
    planType: "",
    subscription: null,
    usage: null,
  }),
  createAccountSummary({
    accountId: "b",
    displayName: "Boreal",
    planType: "plus",
    subscription: { activeUntil: "2026-10-01T00:00:00Z", lastCheckedAt: null },
    usage: { primaryRemainingPercent: 0, secondaryRemainingPercent: 75 },
    availableResetCredits: 3,
  }),
  createAccountSummary({
    accountId: "d",
    displayName: "Dawn",
    planType: "team",
    subscription: { activeUntil: "2020-01-01T00:00:00Z", lastCheckedAt: null },
    usage: { primaryRemainingPercent: 12.5, secondaryRemainingPercent: 30 },
  }),
];
const props = {
  accounts,
  viewMode: "list" as const,
  selectedAccountId: "b",
  onSelect: () => {},
  onOpenImport: () => {},
  onOpenOauth: () => {},
};
const rowNames = () =>
  screen
    .getAllByTestId("account-list-overview-row")
    .map((row) => row.querySelector("p.font-semibold")?.textContent);

beforeEach(() =>
  useAccountQuotaDisplayStore.setState({ quotaDisplay: "both" }),
);
afterEach(() => useAccountQuotaDisplayStore.setState({ quotaDisplay: "both" }));

describe("Accounts List sorting controls", () => {
  it.each([
    {
      label: "Plan",
      asc: ["Boreal", "Atlas", "Dawn"],
      desc: ["Dawn", "Atlas", "Boreal"],
      mode: "Plan (Z-A)",
    },
    {
      label: "Subscription",
      asc: ["Dawn", "Boreal", "Atlas"],
      desc: ["Atlas", "Boreal", "Dawn"],
      mode: "Subscription (latest)",
    },
    {
      label: "Quota 5h",
      asc: ["Boreal", "Dawn", "Atlas"],
      desc: ["Atlas", "Dawn", "Boreal"],
      mode: "5h quota (highest remaining)",
    },
    {
      label: "Quota 7d",
      asc: ["Atlas", "Dawn", "Boreal"],
      desc: ["Boreal", "Dawn", "Atlas"],
      mode: "7d quota (highest remaining)",
    },
  ])(
    "sorts $label in both directions with missing values last",
    async ({ label, asc, desc, mode }) => {
      const user = userEvent.setup();
      // Quota sorting is independent of which quota the appearance preference displays.
      useAccountQuotaDisplayStore.setState({
        quotaDisplay: label === "Quota 5h" ? "weekly" : "5h",
      });
      render(<AccountList {...props} />);
      const header = screen.getByRole("button", {
        name: `${label}: Not sorted`,
      });
      await user.click(header);
      expect(rowNames()).toEqual([...asc, "Cedar", "Echo"]);
      expect(header).toHaveAccessibleName(`${label}: Ascending`);
      expect(header).toHaveAttribute("aria-pressed", "true");
      header.focus();
      await user.keyboard("{Enter}");
      expect(rowNames()).toEqual([...desc, "Cedar", "Echo"]);
      expect(header).toHaveAccessibleName(`${label}: Descending`);
      expect(
        screen.getByRole("combobox", { name: "Sort accounts" }),
      ).toHaveTextContent(mode);
      expect(
        within(
          screen.getByRole("button", { name: /Boreal.*Reset \(3\)/ }),
        ).getByText("Reset (3)"),
      ).toBeInTheDocument();
    },
  );

  it("offers all new sort directions through the dropdown", async () => {
    const user = userEvent.setup();
    render(<AccountList {...props} />);
    for (const [mode, expected] of [
      ["Plan (A-Z)", "Boreal"],
      ["Plan (Z-A)", "Dawn"],
      ["Subscription (soonest)", "Dawn"],
      ["Subscription (latest)", "Atlas"],
      ["5h quota (lowest remaining)", "Boreal"],
      ["5h quota (highest remaining)", "Atlas"],
      ["7d quota (lowest remaining)", "Atlas"],
      ["7d quota (highest remaining)", "Boreal"],
    ]) {
      await user.click(screen.getByRole("combobox", { name: "Sort accounts" }));
      await user.click(screen.getByRole("option", { name: mode }));
      expect(rowNames()[0]).toBe(expected);
      expect(rowNames().slice(-2)).toEqual(["Cedar", "Echo"]);
    }
  });

  it("sorts the whole filtered collection, returns to page one and keeps selection", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 26 }, (_, index) =>
      createAccountSummary({
        accountId: `fleet-${index}`,
        displayName: `Fleet ${String(index).padStart(2, "0")}`,
        usage: {
          primaryRemainingPercent: index,
          secondaryRemainingPercent: 100 - index,
        },
      }),
    );
    render(
      <AccountList
        {...props}
        accounts={[
          ...many,
          createAccountSummary({
            accountId: "outside",
            displayName: "Outside",
          }),
        ]}
        selectedAccountId="fleet-25"
      />,
    );
    await user.type(screen.getByPlaceholderText("Search accounts..."), "Fleet");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("25–26 of 26 accounts")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Quota 5h: Not sorted" }),
    );
    expect(screen.getByText("1–24 of 26 accounts")).toBeInTheDocument();
    expect(rowNames()[0]).toBe("Fleet 00");
    await user.click(
      screen.getByRole("button", { name: "Quota 5h: Ascending" }),
    );
    expect(rowNames()[0]).toBe("Fleet 25");
    expect(screen.getAllByTestId("account-list-overview-row")[0]).toHaveClass(
      "border-primary/30",
    );
    expect(screen.getByPlaceholderText("Search accounts...")).toHaveValue(
      "Fleet",
    );
  });

  it("honors the existing reset badge setting in List", () => {
    const view = render(<AccountList {...props} />);
    expect(screen.getByText("Reset (3)")).toBeInTheDocument();
    view.rerender(<AccountList {...props} showResetCreditBadges={false} />);
    expect(screen.queryByText("Reset (3)")).not.toBeInTheDocument();
  });
});
