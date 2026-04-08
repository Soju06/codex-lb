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
            eligibleRouteFamilies: [],
            additionalQuotas: [],
          },
          {
            accountId: "acc-2",
            email: "secondary@example.com",
            displayName: "Secondary",
            planType: "pro",
            status: "paused",
            eligibleRouteFamilies: [],
            additionalQuotas: [],
          },
        ]}
        selectedAccountId="acc-1"
        platformIdentityRegistered={false}
        platformPrerequisiteSatisfied
        onSelect={onSelect}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
        onOpenPlatform={() => {}}
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
            eligibleRouteFamilies: [],
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        platformIdentityRegistered={false}
        platformPrerequisiteSatisfied
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
        onOpenPlatform={() => {}}
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
            eligibleRouteFamilies: [],
            additionalQuotas: [],
          },
          {
            accountId: "7f9de2ad-7621-4a6f-88bc-ec7f3d914701_91a95cee",
            email: "dup@example.com",
            displayName: "Duplicate B",
            planType: "plus",
            status: "active",
            eligibleRouteFamilies: [],
            additionalQuotas: [],
          },
          {
            accountId: "acc-3",
            email: "unique@example.com",
            displayName: "Unique",
            planType: "pro",
            status: "active",
            eligibleRouteFamilies: [],
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        platformIdentityRegistered={false}
        platformPrerequisiteSatisfied
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
        onOpenPlatform={() => {}}
      />,
    );

    expect(screen.getByText((_content, el) => el?.tagName === "P" && !!el.textContent?.match(/dup@example\.com \| ID d48f0bfc\.\.\.12b5d5/))).toBeInTheDocument();
    expect(screen.getByText((_content, el) => el?.tagName === "P" && !!el.textContent?.match(/dup@example\.com \| ID 7f9de2ad\.\.\.a95cee/))).toBeInTheDocument();
    expect(screen.getByText("unique@example.com")).toBeInTheDocument();
    expect(screen.queryByText((_content, el) => el?.tagName === "P" && !!el.textContent?.match(/unique@example\.com \| ID/))).not.toBeInTheDocument();
  });

  it("opens the platform dialog from the list toolbar", async () => {
    const user = userEvent.setup();
    const onOpenPlatform = vi.fn();

    render(
      <AccountList
        accounts={[]}
        selectedAccountId={null}
        platformIdentityRegistered={false}
        platformPrerequisiteSatisfied
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
        onOpenPlatform={onOpenPlatform}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Add API Key" }));
    expect(onOpenPlatform).toHaveBeenCalledTimes(1);
  });

  it("disables Add API Key when a platform identity is already registered", () => {
    render(
      <AccountList
        accounts={[]}
        selectedAccountId={null}
        platformIdentityRegistered
        platformPrerequisiteSatisfied
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
        onOpenPlatform={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: "Add API Key" })).toBeDisabled();
    expect(
      screen.getByText("A Platform fallback key is already registered. Phase 1 allows only one."),
    ).toBeInTheDocument();
  });

  it("matches platform identities by routing subject search", async () => {
    const user = userEvent.setup();

    render(
      <AccountList
        accounts={[
          {
            accountId: "platform-1",
            email: "Platform Key",
            displayName: "Platform Key",
            planType: "openai_platform",
            status: "active",
            providerKind: "openai_platform",
            routingSubjectId: "subject-platform-1",
            eligibleRouteFamilies: [],
            additionalQuotas: [],
          },
          {
            accountId: "acc-2",
            email: "secondary@example.com",
            displayName: "Secondary",
            planType: "pro",
            status: "active",
            eligibleRouteFamilies: [],
            additionalQuotas: [],
          },
        ]}
        selectedAccountId={null}
        platformIdentityRegistered={false}
        platformPrerequisiteSatisfied
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
        onOpenPlatform={() => {}}
      />,
    );

    await user.type(screen.getByPlaceholderText("Search accounts..."), "subject-platform-1");

    expect(screen.getByText(/Subject subject-platform-1/)).toBeInTheDocument();
    expect(screen.queryByText("secondary@example.com")).not.toBeInTheDocument();
  });

  it("disables Add API Key until an active ChatGPT account exists", () => {
    render(
      <AccountList
        accounts={[]}
        selectedAccountId={null}
        platformIdentityRegistered={false}
        platformPrerequisiteSatisfied={false}
        onSelect={() => {}}
        onOpenImport={() => {}}
        onOpenOauth={() => {}}
        onOpenPlatform={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: "Add API Key" })).toBeDisabled();
    expect(
      screen.getByText("Add or reactivate a ChatGPT account before registering a Platform fallback key."),
    ).toBeInTheDocument();
  });
});
