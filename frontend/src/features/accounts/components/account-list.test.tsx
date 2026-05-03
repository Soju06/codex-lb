import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountList } from "@/features/accounts/components/account-list";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";

describe("AccountList", () => {
  beforeEach(() => {
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "both" });
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-01-01T12:00:00.000Z").getTime());
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
            additionalQuotas: [],
          },
          {
            accountId: "acc-2",
            email: "secondary@example.com",
            displayName: "Secondary",
            planType: "pro",
            status: "paused",
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

    await user.type(screen.getByPlaceholderText("Search accounts..."), "secondary");
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

    expect(screen.getAllByText(/^(Hidden Early|Visible Early)$/).map((el) => el.textContent)).toEqual([
      "Visible Early",
      "Hidden Early",
    ]);
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

    expect(screen.getAllByText(/^(Fresh|Stale)$/).map((el) => el.textContent)).toEqual([
      "Fresh",
      "Stale",
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
            additionalQuotas: [],
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

  it("shows account id only for duplicate emails", () => {
    render(
      <AccountList
        accounts={[
          {
            accountId: "d48f0bfc-8ea6-48a7-8d76-d0e5ef1816c5_6f12b5d5",
            email: "dup@example.com",
            displayName: "Duplicate A",
            planType: "plus",
            status: "active",
            additionalQuotas: [],
          },
          {
            accountId: "7f9de2ad-7621-4a6f-88bc-ec7f3d914701_91a95cee",
            email: "dup@example.com",
            displayName: "Duplicate B",
            planType: "plus",
            status: "active",
            additionalQuotas: [],
          },
          {
            accountId: "acc-3",
            email: "unique@example.com",
            displayName: "Unique",
            planType: "pro",
            status: "active",
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
      />,
    );

    expect(screen.getByText((_content, el) => el?.tagName === "P" && !!el.textContent?.match(/dup@example\.com \| ID d48f0bfc\.\.\.12b5d5/))).toBeInTheDocument();
    expect(screen.getByText((_content, el) => el?.tagName === "P" && !!el.textContent?.match(/dup@example\.com \| ID 7f9de2ad\.\.\.a95cee/))).toBeInTheDocument();
    expect(screen.getByText("unique@example.com")).toBeInTheDocument();
    expect(screen.queryByText((_content, el) => el?.tagName === "P" && !!el.textContent?.match(/unique@example\.com \| ID/))).not.toBeInTheDocument();
  });
});
