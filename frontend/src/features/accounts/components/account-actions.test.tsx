import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountActions } from "@/features/accounts/components/account-actions";
import { createAccountSummary } from "@/test/mocks/factories";

const noopHandlers = {
  onPause: vi.fn(),
  onResume: vi.fn(),
  onProbe: vi.fn(),
  onApplyUsageReset: vi.fn(),
  onDelete: vi.fn(),
  onReauth: vi.fn(),
  onExportAuth: vi.fn(),
  onSecurityWorkAuthorizedChange: vi.fn(),
  onLimitWarmupChange: vi.fn(),
  onRoutingPolicyChange: vi.fn(),
};

describe("AccountActions", () => {
  it("renders an explicit routing policy selector", async () => {
    const onRoutingPolicyChange = vi.fn();
    const account = createAccountSummary({ routingPolicy: "normal" });

    render(
      <AccountActions
        account={account}
        busy={false}
        {...noopHandlers}
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
        {...noopHandlers}
        onReauth={onReauth}
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
        {...noopHandlers}
        onProbe={onProbe}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Force probe" }));

    expect(onProbe).toHaveBeenCalledWith(account.accountId);
    expect(onProbe).toHaveBeenCalledTimes(1);
  });

  it("fires the apply reset callback when saved credits are available", async () => {
    const user = userEvent.setup();
    const account = createAccountSummary({ rateLimitResetAvailableCount: 1 });
    const onApplyUsageReset = vi.fn();

    render(
      <AccountActions
        account={account}
        busy={false}
        {...noopHandlers}
        onApplyUsageReset={onApplyUsageReset}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Apply reset" }));

    expect(onApplyUsageReset).toHaveBeenCalledWith(account.accountId);
    expect(onApplyUsageReset).toHaveBeenCalledTimes(1);
  });

  it("disables apply reset for paused accounts even when credits are available", async () => {
    const user = userEvent.setup();
    const account = createAccountSummary({
      status: "paused",
      rateLimitResetAvailableCount: 1,
    });
    const onApplyUsageReset = vi.fn();

    render(
      <AccountActions
        account={account}
        busy={false}
        {...noopHandlers}
        onApplyUsageReset={onApplyUsageReset}
      />,
    );

    const button = screen.getByRole("button", { name: "Apply reset" });
    expect(button).toBeDisabled();

    await user.click(button);

    expect(onApplyUsageReset).not.toHaveBeenCalled();
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
          {...noopHandlers}
          onProbe={onProbe}
        />,
      );

      const button = screen.getByRole("button", { name: "Force probe" });
      expect(button).toBeDisabled();

      await user.click(button);

      expect(onProbe).not.toHaveBeenCalled();
    },
  );

  it("disables apply reset when no saved credits are available", async () => {
    const user = userEvent.setup();
    const account = createAccountSummary({ rateLimitResetAvailableCount: 0 });
    const onApplyUsageReset = vi.fn();

    render(
      <AccountActions
        account={account}
        busy={false}
        {...noopHandlers}
        onApplyUsageReset={onApplyUsageReset}
      />,
    );

    const button = screen.getByRole("button", { name: "Apply reset" });
    expect(button).toBeDisabled();

    await user.click(button);

    expect(onApplyUsageReset).not.toHaveBeenCalled();
  });

  it("disables force probe in read-only mode", async () => {
    const user = userEvent.setup();
    const account = createAccountSummary();
    const onProbe = vi.fn();

    render(
      <AccountActions
        account={account}
        busy={false}
        readOnly
        {...noopHandlers}
        onProbe={onProbe}
      />,
    );

    const button = screen.getByRole("button", { name: "Force probe" });
    expect(button).toBeDisabled();

    await user.click(button);

    expect(onProbe).not.toHaveBeenCalled();
  });

  it("disables apply reset in read-only mode", async () => {
    const user = userEvent.setup();
    const account = createAccountSummary({ rateLimitResetAvailableCount: 2 });
    const onApplyUsageReset = vi.fn();

    render(
      <AccountActions
        account={account}
        busy={false}
        readOnly
        {...noopHandlers}
        onApplyUsageReset={onApplyUsageReset}
      />,
    );

    const button = screen.getByRole("button", { name: "Apply reset" });
    expect(button).toBeDisabled();

    await user.click(button);

    expect(onApplyUsageReset).not.toHaveBeenCalled();
  });
});