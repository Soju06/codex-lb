import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountActions } from "@/features/accounts/components/account-actions";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountActions", () => {
  it("renders an explicit routing policy selector", async () => {
    const onRoutingPolicyChange = vi.fn();
    const account = createAccountSummary({ routingPolicy: "normal" });

    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onSecurityWorkAuthorizedChange={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={onRoutingPolicyChange}
      />,
    );

    expect(screen.getByText("Routing policy")).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Routing policy" }),
    ).toHaveTextContent("Normal");
  });

  it("renders re-authenticate action for re-auth required accounts", () => {
    const onReauth = vi.fn();
    const account = createAccountSummary({ status: "reauth_required" });

    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={vi.fn()}
        onDelete={vi.fn()}
        onReauth={onReauth}
        onExportAuth={vi.fn()}
        onSecurityWorkAuthorizedChange={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Re-authenticate" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Pause" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: "Routing policy" }),
    ).not.toBeInTheDocument();
  });

  it("fires the per-account probe callback for active accounts", async () => {
    const user = userEvent.setup();
    const account = createAccountSummary();
    const onProbe = vi.fn();

    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onProbe={onProbe}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onExportAuth={vi.fn()}
        onSecurityWorkAuthorizedChange={vi.fn()}
        onLimitWarmupChange={vi.fn()}
        onRoutingPolicyChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Force probe" }));

    expect(onProbe).toHaveBeenCalledWith(account.accountId);
    expect(onProbe).toHaveBeenCalledTimes(1);
  });

  it.each(["paused", "deactivated"] as const)(
    "disables force probe for %s accounts",
    async (status) => {
      const user = userEvent.setup();
      const account = createAccountSummary({ status });
      const onProbe = vi.fn();

      render(
        <AccountActions
          account={account}
          busy={false}
          onPause={vi.fn()}
          onResume={vi.fn()}
          onProbe={onProbe}
          onDelete={vi.fn()}
          onReauth={vi.fn()}
          onExportAuth={vi.fn()}
          onSecurityWorkAuthorizedChange={vi.fn()}
          onLimitWarmupChange={vi.fn()}
          onRoutingPolicyChange={vi.fn()}
        />,
      );

      const button = screen.getByRole("button", { name: "Force probe" });
      expect(button).toBeDisabled();

      await user.click(button);

      expect(onProbe).not.toHaveBeenCalled();
    },
  );
});
