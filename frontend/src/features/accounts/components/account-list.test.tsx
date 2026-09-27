import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountList } from "@/features/accounts/components/account-list";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountList", () => {
  it("bounds grid rendering, navigates pages, and resets pagination when filtering", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const accounts = Array.from({ length: 26 }, (_, i) => createAccountSummary({
      accountId: `acc-${i}`, displayName: `Account ${String(i).padStart(2, "0")}`, email: `user${i}@example.com`,
    }));
    render(<AccountList accounts={accounts} viewMode="grid" selectedAccountId={null} onSelect={onSelect} onOpenImport={() => {}} onOpenOauth={() => {}} />);
    expect(screen.getAllByTestId("account-grid-card")).toHaveLength(24);
    expect(screen.getByText("1–24 of 26 accounts")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getAllByTestId("account-grid-card")).toHaveLength(2);
    expect(screen.getByText("25–26 of 26 accounts")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "View details" })[0]);
    expect(onSelect).toHaveBeenCalledWith("acc-24");
    await user.type(screen.getByPlaceholderText("Search accounts..."), "user0@");
    expect(screen.getAllByTestId("account-grid-card")).toHaveLength(1);
    expect(screen.getByText("1–1 of 1 accounts")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
  });

  it("retains the compact scrollable selector in Detail view", () => {
    render(<AccountList viewMode="detail" accounts={Array.from({ length: 26 }, (_, i) => createAccountSummary({ accountId: `account-${i}`, displayName: `Account ${i}` }))} selectedAccountId={null} onSelect={() => {}} onOpenImport={() => {}} onOpenOauth={() => {}} />);
    const region = screen.getByTestId("account-list-scroll-region");
    expect(region).toHaveClass("overflow-y-auto");
    expect(region.querySelectorAll("button")).toHaveLength(26);
    expect(screen.queryByTestId("account-list-overview-row")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
    expect(region).not.toContainElement(screen.getByRole("button", { name: "Add account" }));
  });

  it("places compact plan time beneath status only in the original selector", () => {
    const account = createAccountSummary({ subscription: { activeUntil: "2026-01-19T20:00:00Z", lastCheckedAt: null } });
    const props = { accounts: [account], selectedAccountId: null, onSelect: () => {}, onOpenImport: () => {}, onOpenOauth: () => {} };
    const view = render(<AccountList {...props} viewMode="detail" />);
    const remaining = screen.getByText("18d 08h");
    expect(remaining.previousElementSibling).toHaveTextContent("Active");
    expect(screen.queryByText("18d 8h remaining")).not.toBeInTheDocument();
    view.rerender(<AccountList {...props} viewMode="grid" />);
    expect(screen.queryByTestId("account-plan-remaining")).not.toBeInTheDocument();
    expect(screen.getByText("18d 8h remaining")).toBeInTheDocument();
  });

  beforeEach(() => {
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "both" });
    vi.spyOn(Date, "now").mockReturnValue(
      new Date("2026-01-01T12:00:00.000Z").getTime(),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

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
            limitWarmupEnabled: false,
            additionalQuotas: [],
          },
          {
            accountId: "acc-2",
            email: "secondary@example.com",
            displayName: "Secondary",
            planType: "pro",
            status: "paused",
            limitWarmupEnabled: false,
            additionalQuotas: [],
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

    await user.type(
      screen.getByPlaceholderText("Search accounts..."),
      "secondary",
    );
    expect(screen.queryByText("primary@example.com")).not.toBeInTheDocument();
    expect(screen.getByText("secondary@example.com")).toBeInTheDocument();

    await user.click(screen.getByText("secondary@example.com"));
    expect(onSelect).toHaveBeenCalledWith("acc-2");
  });

  it("sorts accounts by the rows actually rendered", () => {
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "weekly" });

    render(
      <AccountList
        accounts={[
          {
            accountId: "acc-hidden-early",
            email: "hidden-early@example.com",
            displayName: "Hidden Early",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            usage: {
              primaryRemainingPercent: 42,
              secondaryRemainingPercent: 18,
            },
            resetAtPrimary: "2026-01-01T12:05:00.000Z",
            resetAtSecondary: "2026-01-01T13:00:00.000Z",
            windowMinutesPrimary: 300,
            windowMinutesSecondary: 10_080,
            additionalQuotas: [],
          },
          {
            accountId: "acc-visible-early",
            email: "visible-early@example.com",
            displayName: "Visible Early",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            usage: {
              primaryRemainingPercent: 82,
              secondaryRemainingPercent: 73,
            },
            resetAtPrimary: "2026-01-01T12:30:00.000Z",
            resetAtSecondary: "2026-01-01T12:10:00.000Z",
            windowMinutesPrimary: 300,
            windowMinutesSecondary: 10_080,
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    expect(
      screen
        .getAllByText(/^(Hidden Early|Visible Early)$/)
        .map((el) => el.textContent),
    ).toEqual(["Visible Early", "Hidden Early"]);
  });

  it("ignores elapsed reset timestamps when sorting", () => {
    render(
      <AccountList
        accounts={[
          {
            accountId: "acc-stale",
            email: "stale@example.com",
            displayName: "Stale",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            usage: {
              primaryRemainingPercent: 42,
              secondaryRemainingPercent: 18,
            },
            resetAtPrimary: "2026-01-01T11:30:00.000Z",
            resetAtSecondary: "2026-01-01T11:45:00.000Z",
            windowMinutesPrimary: 300,
            windowMinutesSecondary: 10_080,
            additionalQuotas: [],
          },
          {
            accountId: "acc-fresh",
            email: "fresh@example.com",
            displayName: "Fresh",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            usage: {
              primaryRemainingPercent: 82,
              secondaryRemainingPercent: 73,
            },
            resetAtPrimary: "2026-01-01T12:30:00.000Z",
            resetAtSecondary: "2026-01-01T12:20:00.000Z",
            windowMinutesPrimary: 300,
            windowMinutesSecondary: 10_080,
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    expect(
      screen.getAllByText(/^(Fresh|Stale)$/).map((el) => el.textContent),
    ).toEqual(["Fresh", "Stale"]);
  });

  it("sorts legacy primary quota rows by their reset timestamp", () => {
    render(
      <AccountList
        accounts={[
          {
            accountId: "acc-late",
            email: "late@example.com",
            displayName: "Late",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            usage: {
              primaryRemainingPercent: 42,
              secondaryRemainingPercent: null,
            },
            resetAtPrimary: "2026-01-01T13:00:00.000Z",
            resetAtSecondary: null,
            windowMinutesPrimary: null,
            windowMinutesSecondary: null,
            additionalQuotas: [],
          },
          {
            accountId: "acc-early",
            email: "early@example.com",
            displayName: "Early",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            usage: {
              primaryRemainingPercent: 82,
              secondaryRemainingPercent: null,
            },
            resetAtPrimary: "2026-01-01T12:10:00.000Z",
            resetAtSecondary: null,
            windowMinutesPrimary: null,
            windowMinutesSecondary: null,
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    expect(
      screen.getAllByText(/^(Early|Late)$/).map((el) => el.textContent),
    ).toEqual(["Early", "Late"]);
  });

  it("sorts accounts by name", () => {
    render(
      <AccountList
        accounts={[
          {
            accountId: "acc-z",
            email: "z@example.com",
            displayName: "Zeta",
            planType: "pro",
            status: "active",
            limitWarmupEnabled: false,
            resetAtPrimary: "2026-01-01T12:30:00.000Z",
            additionalQuotas: [],
          },
          {
            accountId: "acc-a",
            email: "a@example.com",
            displayName: "Alpha",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            resetAtPrimary: "2026-01-01T12:10:00.000Z",
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
        sortMode="name_asc"
        onSortModeChange={() => {}}
      />,
    );

    expect(screen.getAllByText(/^(Alpha|Zeta)$/).map((el) => el.textContent)).toEqual([
      "Alpha",
      "Zeta",
    ]);
  });

  it("supports reverse name sorting", () => {
    render(
      <AccountList
        accounts={[
          {
            accountId: "acc-b",
            email: "b@example.com",
            displayName: "Beta",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            resetAtPrimary: "2026-01-01T12:10:00.000Z",
            additionalQuotas: [],
          },
          {
            accountId: "acc-a",
            email: "a@example.com",
            displayName: "Alpha",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            resetAtPrimary: "2026-01-01T12:20:00.000Z",
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
        sortMode="name_desc"
        onSortModeChange={() => {}}
      />,
    );

    expect(screen.getAllByText(/^(Alpha|Beta)$/).map((el) => el.textContent)).toEqual([
      "Beta",
      "Alpha",
    ]);
  });

  it("can sort by latest reset first", () => {
    render(
      <AccountList
        accounts={[
          {
            accountId: "acc-a",
            email: "a@example.com",
            displayName: "Alpha",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            resetAtPrimary: "2026-01-01T12:10:00.000Z",
            additionalQuotas: [],
          },
          {
            accountId: "acc-z",
            email: "z@example.com",
            displayName: "Zeta",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            resetAtPrimary: "2026-01-01T12:40:00.000Z",
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
        sortMode="reset_latest"
        onSortModeChange={() => {}}
      />,
    );

    expect(screen.getAllByText(/^(Zeta|Alpha)$/).map((el) => el.textContent)).toEqual([
      "Zeta",
      "Alpha",
    ]);
  });

  it("keeps unknown resets last when sorting by latest reset", () => {
    render(
      <AccountList
        accounts={[
          {
            accountId: "acc-unknown",
            email: "unknown@example.com",
            displayName: "Unknown",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            additionalQuotas: [],
          },
          {
            accountId: "acc-stale",
            email: "stale@example.com",
            displayName: "Stale",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            resetAtPrimary: "2026-01-01T11:30:00.000Z",
            additionalQuotas: [],
          },
          {
            accountId: "acc-latest",
            email: "latest@example.com",
            displayName: "Latest",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            resetAtPrimary: "2026-01-01T12:40:00.000Z",
            additionalQuotas: [],
          },
          {
            accountId: "acc-earlier",
            email: "earlier@example.com",
            displayName: "Earlier",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            resetAtPrimary: "2026-01-01T12:10:00.000Z",
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
        sortMode="reset_latest"
        onSortModeChange={() => {}}
      />,
    );

    expect(screen.getAllByText(/^(Latest|Earlier|Stale|Unknown)$/, { selector: "p.font-semibold" }).map((el) => el.textContent)).toEqual([
      "Latest",
      "Earlier",
      "Stale",
      "Unknown",
    ]);
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
            limitWarmupEnabled: false,
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    await user.type(
      screen.getByPlaceholderText("Search accounts..."),
      "not-found",
    );
    expect(screen.getByText("No matching accounts")).toBeInTheDocument();
  });

  it("shows first-run empty copy when no accounts exist", () => {
    render(
      <AccountList
        accounts={[]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    expect(screen.getByText("No accounts yet")).toBeInTheDocument();
    expect(screen.getByText("Add an account to start routing.")).toBeInTheDocument();
    expect(screen.queryByText("Adjust filters")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Individual requests can still skip an Active account/i),
    ).not.toBeInTheDocument();
  });

  it("shows a visible status-vs-eligibility note when accounts exist", () => {
    render(
      <AccountList
        accounts={[
          {
            accountId: "acc-1",
            email: "primary@example.com",
            displayName: "Primary",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    expect(
      screen.getByText(/Individual requests can still skip an Active account/i),
    ).toBeInTheDocument();
  });

  it("keeps the add account action outside the scrollable account list", () => {
    render(
      <AccountList
        accounts={[
          {
            accountId: "acc-1",
            email: "primary@example.com",
            displayName: "Primary",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    const addAccountButton = screen.getByRole("button", { name: "Add account" });
    const scrollRegion = screen.getByTestId("account-list-scroll-region");

    expect(scrollRegion).not.toContainElement(addAccountButton);
  });

  it("paginates the full-width list without losing remaining accounts", async () => {
    const user = userEvent.setup();
    render(
      <AccountList
        accounts={Array.from({ length: 26 }, (_, index) => ({
          accountId: `acc-${index}`,
          email: `account-${index}@example.com`,
          displayName: `Account ${index}`,
          planType: "plus",
          status: "active",
          limitWarmupEnabled: false,
          additionalQuotas: [],
        }))}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    expect(screen.getAllByTestId("account-list-overview-row")).toHaveLength(24);
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getAllByTestId("account-list-overview-row")).toHaveLength(2);
    await user.type(screen.getByPlaceholderText("Search accounts..."), "account-0@");
    expect(screen.getAllByTestId("account-list-overview-row")).toHaveLength(1);
  });

  it("filters re-auth required accounts by status", async () => {
    const user = userEvent.setup();

    render(
      <AccountList
        accounts={[
          {
            accountId: "acc-active",
            email: "active@example.com",
            displayName: "Active",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            additionalQuotas: [],
          },
          {
            accountId: "acc-reauth",
            email: "reauth@example.com",
            displayName: "Needs Reauth",
            planType: "pro",
            status: "reauth_required",
            limitWarmupEnabled: false,
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    await user.click(screen.getByRole("combobox", { name: "Filter accounts by status" }));
    await user.click(screen.getByRole("option", { name: "Reauth required" }));

    expect(screen.queryByText("active@example.com")).not.toBeInTheDocument();
    expect(screen.getByText("reauth@example.com")).toBeInTheDocument();
  });

  it("uses the backend duplicate indicator instead of recomputing by email", () => {
    render(
      <AccountList
        accounts={[
          {
            accountId: "d48f0bfc-8ea6-48a7-8d76-d0e5ef1816c5_6f12b5d5",
            email: "dup@example.com",
            displayName: "Same email, different workspace",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            isEmailDuplicate: false,
            additionalQuotas: [],
          },
          {
            accountId: "7f9de2ad-7621-4a6f-88bc-ec7f3d914701_91a95cee",
            email: "dup@example.com",
            displayName: "Same email, duplicate slot",
            planType: "plus",
            status: "active",
            limitWarmupEnabled: false,
            isEmailDuplicate: true,
            additionalQuotas: [],
          },
          {
            accountId: "acc-3",
            email: "unique@example.com",
            displayName: "Unique",
            planType: "pro",
            status: "active",
            limitWarmupEnabled: false,
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    expect(
      screen.queryByText(
        (_content, el) =>
          el?.tagName === "P" &&
          !!el.textContent?.match(
            /ID d48f0bfc\.\.\.12b5d5/,
          ),
      ),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        (_content, el) =>
          el?.tagName === "P" &&
          !!el.textContent?.match(
            /ID 7f9de2ad\.\.\.a95cee/,
          ),
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        (_content, el) =>
          el?.tagName === "P" &&
          !!el.textContent?.match(/unique@example\.com \| ID/),
      ),
    ).not.toBeInTheDocument();
  });
});
