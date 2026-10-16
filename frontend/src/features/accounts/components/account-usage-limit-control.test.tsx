import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountUsageLimitControl } from "@/features/accounts/components/account-usage-limit-control";
import type { AccountSummary } from "@/features/accounts/schemas";
import { createAccountSummary } from "@/test/mocks/factories";

function control(account: AccountSummary, onChange = vi.fn(), busy = false) {
  return <AccountUsageLimitControl account={account} busy={busy} readOnly={false} onChange={onChange} />;
}

describe("AccountUsageLimitControl", () => {
  it("explains, toggles, edits, and removes a retained limit", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      usageLimitEnabled: false,
      usageLimitPercent: 10,
      usageLimitState: "disabled",
    });

    render(control(account, onChange));

    expect(screen.getAllByText("Off")).toHaveLength(1);
    expect(screen.getByRole("switch", { name: "Protect reserved quota" })).not.toBeChecked();

    await user.click(screen.getByRole("switch", { name: "Protect reserved quota" }));
    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true,
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

    render(control(account, onChange));

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

  it("accepts a zero reserve as full window use", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({ usageLimitPercent: null, usageLimitEnabled: false });
    render(control(account, onChange));

    const input = screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" });
    await user.type(input, "0");
    expect(input).toHaveAttribute("aria-invalid", "false");
    await user.click(screen.getByRole("button", { name: "Save and enable" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true,
      percent: 100,
      percent5H: null,
      percentWeekly: null,
    });
  });

  it("distinguishes a reached local limit", () => {
    render(
      control(createAccountSummary({
          usageLimitEnabled: true,
          usageLimitPercent: 10,
          usageLimitState: "reached",
        }), vi.fn()),
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
    const { rerender } = render(control(account, onChange));
    const input = screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" });

    await user.clear(input);
    await user.type(input, "87.5");
    rerender(control({ ...account, usageLimitState: "reached" }, onChange));

    expect(input).toHaveFocus();
    expect(input).toHaveValue(87.5);

    rerender(control({ ...account, usageLimitPercent: 20 }, onChange));
    expect(input).toHaveFocus();
    expect(input).toHaveValue(80);

    await user.clear(input);
    await user.type(input, "70");
    rerender(control({ ...account, accountId: "acc_secondary" }, onChange));
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

    const { rerender } = render(control(account, onChange));

    const input = screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" });
    await user.type(input, "{Enter}");
    expect(onChange).not.toHaveBeenCalled();

    rerender(control(account, onChange, true));
    expect(screen.getByRole("switch", { name: "Protect reserved quota" })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove saved reserves" })).toBeDisabled();
  });

  it("explains invalid percentages and clears the error after correction", async () => {
    const user = userEvent.setup();
    render(control(createAccountSummary({ usageLimitPercent: null }), vi.fn()));

    const input = screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" });
    const save = screen.getByRole("button", { name: "Save and enable" });
    await user.type(input, "100");

    expect(input).toHaveAttribute("max", "99.99999999999999");
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

      render(control(account, onChange));

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
    render(control(account, onChange));
    await user.type(screen.getByRole("spinbutton", { name: "5-hour reserve (%)" }), "30");
    await user.type(screen.getByRole("spinbutton", { name: "Weekly reserve (%)" }), "10");
    await user.click(screen.getByRole("button", { name: "Save and enable" }));
    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true, percent: null, percent5H: 70, percentWeekly: 90,
    });
  });
  it("shows weekly overrides only for a weekly-only account and preserves hidden saved overrides", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      planType: "pro", windowMinutesPrimary: null, windowMinutesSecondary: 10080,
      usage: { primaryRemainingPercent: null, secondaryRemainingPercent: 83 },
      usageLimitPercent: 80, usageLimit5HPercent: 60, usageLimitEnabled: true,
    });
    render(control(account, onChange));
    expect(screen.queryByRole("spinbutton", { name: "5-hour reserve (%)" })).not.toBeInTheDocument();
    await user.type(screen.getByRole("spinbutton", { name: "Weekly reserve (%)" }), "30");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true, percent: 80, percent5H: 60, percentWeekly: 70,
    });
  });

  it("edits monthly reserves through the shared policy and retains it across disable and re-enable", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      planType: "free", windowMinutesPrimary: null, windowMinutesSecondary: null, windowMinutesMonthly: 43200,
      usage: { primaryRemainingPercent: null, secondaryRemainingPercent: null, monthlyRemainingPercent: 85 },
    });
    const { rerender } = render(control(account, onChange));
    expect(screen.getAllByRole("spinbutton")).toHaveLength(1);
    await user.type(screen.getByRole("spinbutton", { name: "Monthly reserve (%)" }), "20");
    await user.click(screen.getByRole("button", { name: "Save and enable" }));
    expect(onChange).toHaveBeenLastCalledWith(account.accountId, {
      enabled: true, percent: 80, percent5H: null, percentWeekly: null,
    });
    rerender(control({ ...account, usageLimitEnabled: true, usageLimitPercent: 80 }, onChange));
    await user.click(screen.getByRole("switch", { name: "Protect reserved quota" }));
    expect(onChange).toHaveBeenLastCalledWith(account.accountId, { enabled: false });
    rerender(control({ ...account, usageLimitEnabled: false, usageLimitPercent: 80 }, onChange));
    await user.click(screen.getByRole("switch", { name: "Protect reserved quota" }));
    expect(onChange).toHaveBeenLastCalledWith(account.accountId, { enabled: true });
  });

  it.each([[null, null, 1], [60, 1440, 1], [10080, null, 2]])(
    "offers matching overrides for reported durations %s and %s", (primary, secondary, count) => {
    render(control(createAccountSummary({
      windowMinutesPrimary: primary, windowMinutesSecondary: secondary, windowMinutesMonthly: null,
      usage: { primaryRemainingPercent: null, secondaryRemainingPercent: null },
    }), vi.fn()));
    expect(screen.getAllByRole("spinbutton")).toHaveLength(count);
    expect(screen.queryByRole("spinbutton", { name: "5-hour reserve (%)" })).not.toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" })).toBeInTheDocument();
  });

});
