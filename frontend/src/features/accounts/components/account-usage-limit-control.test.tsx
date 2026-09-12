import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountUsageLimitControl } from "@/features/accounts/components/account-usage-limit-control";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountUsageLimitControl", () => {
  it("explains, toggles, edits, and removes a retained limit", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      usageLimitEnabled: false,
      usageLimitPercent: 10,
      usageLimitState: "disabled",
    });

    render(
      <AccountUsageLimitControl
        account={account}
        busy={false}
        readOnly={false}
        onChange={onChange}
      />,
    );

    expect(screen.getAllByText("Off")).toHaveLength(1);
    expect(screen.getByRole("switch", { name: "Protect reserved quota" })).not.toBeChecked();

    await user.click(screen.getByRole("switch", { name: "Protect reserved quota" }));
    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true,
      percent: 10,
      percent5H: null,
      percentWeekly: null,
    });

    const input = screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" });
    await user.clear(input);
    await user.type(input, "87.5");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: false,
      percent: 12.5,
      percent5H: null,
      percentWeekly: null,
    });

    await user.click(screen.getByRole("button", { name: "Remove saved reserves" }));
    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: false,
      percent: null,
      percent5H: null,
      percentWeekly: null,
    });
  });

  it("sets and enables a new limit", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      usageLimitEnabled: false,
      usageLimitPercent: null,
      usageLimitState: "disabled",
    });

    render(
      <AccountUsageLimitControl
        account={account}
        busy={false}
        readOnly={false}
        onChange={onChange}
      />,
    );

    await user.type(
      screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" }),
      "90",
    );
    await user.click(screen.getByRole("button", { name: "Save and enable" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true,
      percent: 10,
      percent5H: null,
      percentWeekly: null,
    });
  });

  it("omits the cached percentage when disabling a configured limit", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      usageLimitEnabled: true,
      usageLimitPercent: 10,
      usageLimitState: "available",
    });

    render(
      <AccountUsageLimitControl
        account={account}
        busy={false}
        readOnly={false}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("switch", { name: "Protect reserved quota" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: false,
    });
  });

  it("distinguishes a reached local limit", () => {
    render(
      <AccountUsageLimitControl
        account={createAccountSummary({
          usageLimitEnabled: true,
          usageLimitPercent: 10,
          usageLimitState: "reached",
        })}
        busy={false}
        readOnly={false}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Reached · routing blocked")).toBeInTheDocument();
  });

  it("preserves focus and a draft until the authoritative account or percentage changes", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      usageLimitEnabled: true,
      usageLimitPercent: 10,
      usageLimitState: "available",
    });
    const { rerender } = render(
      <AccountUsageLimitControl
        account={account}
        busy={false}
        readOnly={false}
        onChange={onChange}
      />,
    );
    const input = screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" });

    await user.clear(input);
    await user.type(input, "87.5");
    rerender(
      <AccountUsageLimitControl
        account={{ ...account, usageLimitState: "reached" }}
        busy={false}
        readOnly={false}
        onChange={onChange}
      />,
    );

    expect(input).toHaveFocus();
    expect(input).toHaveValue(87.5);

    rerender(
      <AccountUsageLimitControl
        account={{ ...account, usageLimitPercent: 20 }}
        busy={false}
        readOnly={false}
        onChange={onChange}
      />,
    );
    expect(input).toHaveFocus();
    expect(input).toHaveValue(80);

    await user.clear(input);
    await user.type(input, "70");
    rerender(
      <AccountUsageLimitControl
        account={{ ...account, accountId: "acc_secondary" }}
        busy={false}
        readOnly={false}
        onChange={onChange}
      />,
    );
    expect(input).toHaveFocus();
    expect(input).toHaveValue(90);
  });

  it("does not save an unchanged draft via Enter and disables controls while busy", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      usageLimitEnabled: true,
      usageLimitPercent: 10,
      usageLimitState: "available",
    });

    const { rerender } = render(
      <AccountUsageLimitControl
        account={account}
        busy={false}
        readOnly={false}
        onChange={onChange}
      />,
    );

    const input = screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" });
    await user.type(input, "{Enter}");
    expect(onChange).not.toHaveBeenCalled();

    rerender(
      <AccountUsageLimitControl
        account={account}
        busy
        readOnly={false}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("switch", { name: "Protect reserved quota" })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove saved reserves" })).toBeDisabled();
  });

  it("explains invalid percentages and clears the error after correction", async () => {
    const user = userEvent.setup();
    render(
      <AccountUsageLimitControl
        account={createAccountSummary({ usageLimitPercent: null })}
        busy={false}
        readOnly={false}
        onChange={vi.fn()}
      />,
    );

    const input = screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" });
    const save = screen.getByRole("button", { name: "Save and enable" });
    await user.type(input, "100");

    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enter a reserve from 0% up to, but not including, 100%.",
    );
    expect(save).toBeDisabled();

    await user.clear(input);
    await user.type(input, "90");

    expect(input).toHaveAttribute("aria-invalid", "false");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(save).toBeEnabled();
  });

  it.each([
    { configured: 1e-7, edited: "99.9999998", saved: 2e-7 },
    { configured: 0.001, edited: "99.998", saved: 0.002 },
    { configured: 99.999, edited: "0.002", saved: 99.998 },
  ])(
    "preserves and saves the configured precision for $configured percent",
    async ({ configured, edited, saved }) => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      const account = createAccountSummary({
        usageLimitEnabled: true,
        usageLimitPercent: configured,
        usageLimitState: "available",
      });

      render(
        <AccountUsageLimitControl
          account={account}
          busy={false}
          readOnly={false}
          onChange={onChange}
        />,
      );

      const input = screen.getByRole("spinbutton", {
        name: "Reserve for yourself (%)",
      });
      expect(input).toHaveValue(Number((100 - configured).toFixed(7)));

      await user.clear(input);
      await user.type(input, edited);
      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(onChange).toHaveBeenCalledWith(account.accountId, {
        enabled: true,
        percent: saved,
        percent5H: null,
        percentWeekly: null,
      });
    },
  );
  it("sets standalone overrides and keeps the default empty", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({ usageLimitEnabled: false, usageLimitPercent: null });
    render(<AccountUsageLimitControl account={account} busy={false} readOnly={false} onChange={onChange} />);
    await user.type(screen.getByRole("spinbutton", { name: "5-hour reserve (%)" }), "30");
    await user.type(screen.getByRole("spinbutton", { name: "Weekly reserve (%)" }), "10");
    await user.click(screen.getByRole("button", { name: "Save and enable" }));
    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true, percent: null, percent5H: 70, percentWeekly: 90,
    });
  });
});
