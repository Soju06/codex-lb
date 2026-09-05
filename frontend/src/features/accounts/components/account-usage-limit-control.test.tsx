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

    expect(screen.getAllByText("10% maximum used · 90% reserved")).toHaveLength(1);
    expect(screen.getAllByText("Off")).toHaveLength(1);
    expect(screen.getByRole("switch", { name: "Usage limit" })).not.toBeChecked();

    await user.click(screen.getByRole("switch", { name: "Usage limit" }));
    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true,
      percent: 10,
    });

    const input = screen.getByRole("spinbutton", { name: "Maximum used percent" });
    await user.clear(input);
    await user.type(input, "12.5");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: false,
      percent: 12.5,
    });

    await user.click(screen.getByRole("button", { name: "Clear saved limit" }));
    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: false,
      percent: null,
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

    expect(screen.queryByText(/usage reporting is delayed/i)).not.toBeInTheDocument();
    await user.type(
      screen.getByRole("spinbutton", { name: "Maximum used percent" }),
      "10",
    );
    expect(screen.getByText(/usage reporting is delayed/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Set and enable" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true,
      percent: 10,
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

    await user.click(screen.getByRole("switch", { name: "Usage limit" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: false,
    });
  });

  it("distinguishes a reached local limit and warns about observation overshoot", () => {
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
    expect(screen.getByText(/in-flight requests may briefly exceed/i)).toBeInTheDocument();
  });

  it("hides the observation-overshoot warning while a saved limit is disabled", () => {
    render(
      <AccountUsageLimitControl
        account={createAccountSummary({
          usageLimitEnabled: false,
          usageLimitPercent: 10,
          usageLimitState: "disabled",
        })}
        busy={false}
        readOnly={false}
        onChange={vi.fn()}
      />,
    );

    expect(screen.queryByText(/in-flight requests may briefly exceed/i)).not.toBeInTheDocument();
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
    const input = screen.getByRole("spinbutton", { name: "Maximum used percent" });

    await user.clear(input);
    await user.type(input, "12.5");
    rerender(
      <AccountUsageLimitControl
        account={{ ...account, usageLimitState: "reached" }}
        busy={false}
        readOnly={false}
        onChange={onChange}
      />,
    );

    expect(input).toHaveFocus();
    expect(input).toHaveValue(12.5);

    rerender(
      <AccountUsageLimitControl
        account={{ ...account, usageLimitPercent: 20 }}
        busy={false}
        readOnly={false}
        onChange={onChange}
      />,
    );
    expect(input).toHaveFocus();
    expect(input).toHaveValue(20);

    await user.clear(input);
    await user.type(input, "30");
    rerender(
      <AccountUsageLimitControl
        account={{ ...account, accountId: "acc_secondary" }}
        busy={false}
        readOnly={false}
        onChange={onChange}
      />,
    );
    expect(input).toHaveFocus();
    expect(input).toHaveValue(10);
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

    const input = screen.getByRole("spinbutton", { name: "Maximum used percent" });
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
    expect(screen.getByRole("switch", { name: "Usage limit" })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: "Maximum used percent" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Clear saved limit" })).toBeDisabled();
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

    const input = screen.getByRole("spinbutton", { name: "Maximum used percent" });
    const save = screen.getByRole("button", { name: "Set and enable" });
    await user.type(input, "0");

    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enter a percentage greater than 0 and no more than 100.",
    );
    expect(save).toBeDisabled();

    await user.clear(input);
    await user.type(input, "10");

    expect(input).toHaveAttribute("aria-invalid", "false");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(save).toBeEnabled();
  });

  it.each([
    { configured: 1e-7, edited: "0.0000002", saved: 2e-7 },
    { configured: 0.001, edited: "0.002", saved: 0.002 },
    { configured: 99.999, edited: "99.998", saved: 99.998 },
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
        name: "Maximum used percent",
      });
      expect(input).toHaveValue(configured);
      expect(screen.getByText(new RegExp(`${configured}% maximum used`))).toBeInTheDocument();

      await user.clear(input);
      await user.type(input, edited);
      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(onChange).toHaveBeenCalledWith(account.accountId, {
        enabled: true,
        percent: saved,
      });
    },
  );
});
