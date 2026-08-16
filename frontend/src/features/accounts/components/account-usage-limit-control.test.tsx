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
    expect(screen.getAllByText("Off")).toHaveLength(2);
    expect(screen.getByRole("switch", { name: "Enable usage limit" })).not.toBeChecked();

    await user.click(screen.getByRole("switch", { name: "Enable usage limit" }));
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
