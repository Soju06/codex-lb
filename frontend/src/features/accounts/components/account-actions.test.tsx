import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountActions } from "@/features/accounts/components/account-actions";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountActions", () => {
  it("shows Edit for platform identities", async () => {
    const user = userEvent.setup();
    const account = createAccountSummary({
      accountId: "platform_1",
      email: "Platform Key",
      displayName: "Platform Key",
      label: "Platform Key",
      planType: "openai_platform",
      providerKind: "openai_platform",
      routingSubjectId: "platform_1",
      usage: null,
      auth: null,
    });
    const onEditPlatform = vi.fn();

    render(
      <AccountActions
        account={account}
        busy={false}
        onEditPlatform={onEditPlatform}
        onPause={() => {}}
        onResume={() => {}}
        onDelete={() => {}}
        onReauth={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Re-authenticate" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(onEditPlatform).toHaveBeenCalledWith(account);
  });

  it("keeps Re-authenticate reserved for deactivated ChatGPT-web accounts", () => {
    render(
      <AccountActions
        account={createAccountSummary({
          accountId: "acc_chatgpt_1",
          email: "primary@example.com",
          displayName: "primary@example.com",
          providerKind: "chatgpt_web",
          status: "deactivated",
        })}
        busy={false}
        onEditPlatform={() => {}}
        onPause={() => {}}
        onResume={() => {}}
        onDelete={() => {}}
        onReauth={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: "Re-authenticate" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });
});
