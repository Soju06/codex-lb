import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountUsageLimitControl } from "@/features/accounts/components/account-usage-limit-control";
import type { AccountSummary } from "@/features/accounts/schemas";
import { createAccountSummary } from "@/test/mocks/factories";

function control(account: AccountSummary, onChange = vi.fn(), busy = false) {
  return <AccountUsageLimitControl account={account} busy={busy} readOnly={false} onChange={onChange} />;
}

const reserve = (name: string) => screen.getByRole("spinbutton", { name });
const row = (name: string) => screen.getByRole("row", { name: new RegExp(`^${name}`) });

describe("AccountUsageLimitControl", () => {
  it("shows the reserve that applies to each window and previews the result before saving", async () => {
    const user = userEvent.setup();
    render(control(createAccountSummary({
      usageLimitEnabled: true,
      usageLimitPercent: 80,
      usageLimit5HPercent: 70,
      usageLimitWeeklyPercent: 90,
      usageLimitState: "available",
      usage: { primaryRemainingPercent: 35, secondaryRemainingPercent: 25 },
    })));

    expect(screen.getAllByRole("spinbutton")).toHaveLength(2);
    expect(reserve("5-hour")).toHaveValue(30);
    expect(reserve("Weekly")).toHaveValue(10);
    expect(within(row("5-hour")).getByText("35%")).toBeInTheDocument();
    expect(within(row("5-hour")).getByText("5%")).toBeInTheDocument();
    expect(within(row("Weekly")).getByText("15%")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();

    await user.clear(reserve("5-hour"));
    await user.type(reserve("5-hour"), "40");

    expect(within(row("5-hour")).getByText("0% · reached")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("saves edited windows as overrides without touching the hidden default", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      usageLimitEnabled: true, usageLimitPercent: 80, usageLimit5HPercent: 70, usageLimitWeeklyPercent: 90,
    });
    render(control(account, onChange));

    await user.clear(reserve("Weekly"));
    await user.type(reserve("Weekly"), "15");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true, percent5H: 70, percentWeekly: 85,
    });
  });

  it("lets a window that matches the default keep inheriting it", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({ usageLimitEnabled: true, usageLimitPercent: 80 });
    render(control(account, onChange));

    expect(reserve("5-hour")).toHaveValue(20);
    expect(reserve("Weekly")).toHaveValue(20);
    await user.clear(reserve("5-hour"));
    await user.type(reserve("5-hour"), "30");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true, percent5H: 70, percentWeekly: null,
    });
  });

  it("sets and enables a new reserve, ignoring an all-zero draft", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({ usageLimitEnabled: false, usageLimitPercent: null });
    render(control(account, onChange));

    expect(screen.getByRole("switch", { name: "Protect reserved quota" })).toBeDisabled();
    await user.type(reserve("5-hour"), "0");
    expect(screen.queryByRole("button", { name: "Save and enable" })).not.toBeInTheDocument();

    await user.clear(reserve("5-hour"));
    await user.type(reserve("5-hour"), "25");
    await user.type(reserve("Weekly"), "25");
    await user.click(screen.getByRole("button", { name: "Save and enable" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true, percent5H: 75, percentWeekly: 75,
    });
  });

  it("removes the policy when every window is cleared", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      usageLimitEnabled: true, usageLimitPercent: 80, usageLimit5HPercent: 70,
    });
    render(control(account, onChange));

    await user.clear(reserve("5-hour"));
    await user.clear(reserve("Weekly"));
    await user.click(screen.getByRole("button", { name: "Remove reserve" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: false, percent: null, percent5H: null, percentWeekly: null,
    });
  });

  it("offers removal when only an override for an unreported window remains", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      planType: "pro", windowMinutesPrimary: null, windowMinutesSecondary: 10080,
      usage: { primaryRemainingPercent: null, secondaryRemainingPercent: 83 },
      usageLimitEnabled: true, usageLimit5HPercent: 70, usageLimitState: "data_unavailable",
    });
    render(control(account, onChange));

    expect(reserve("Weekly")).toHaveValue(null);
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove reserve" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: false, percent: null, percent5H: null, percentWeekly: null,
    });
  });

  it("keeps a tiny saved cap valid while another row is edited", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      usageLimitEnabled: true, usageLimit5HPercent: 1e-15, usageLimitWeeklyPercent: 80,
    });
    render(control(account, onChange));

    expect(reserve("5-hour")).toHaveAttribute("aria-invalid", "false");
    await user.clear(reserve("Weekly"));
    await user.type(reserve("Weekly"), "30");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true, percent5H: 1e-15, percentWeekly: 70,
    });
  });

  it("turns a saved reserve on and off without re-entering it", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      usageLimitEnabled: false, usageLimitPercent: 10, usageLimitState: "disabled",
      usage: { primaryRemainingPercent: 82, secondaryRemainingPercent: 67 },
    });
    const { rerender } = render(control(account, onChange));

    expect(screen.getByText(/^Off\. Codex LB can use all quota/)).toBeInTheDocument();
    expect(reserve("5-hour")).toHaveValue(90);
    expect(within(row("5-hour")).getAllByText("82%")).toHaveLength(2);
    await user.click(screen.getByRole("switch", { name: "Protect reserved quota" }));
    expect(onChange).toHaveBeenLastCalledWith(account.accountId, { enabled: true });

    rerender(control({ ...account, usageLimitEnabled: true, usageLimitState: "available" }, onChange));
    await user.click(screen.getByRole("switch", { name: "Protect reserved quota" }));
    expect(onChange).toHaveBeenLastCalledWith(account.accountId, { enabled: false });
  });

  it.each([
    ["reached", "Limit reached"],
    ["data_unavailable", "Usage unavailable"],
  ] as const)("labels a %s policy without an Active badge", (state, label) => {
    render(control(createAccountSummary({ usageLimitEnabled: true, usageLimitPercent: 10, usageLimitState: state })));

    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText("Active")).not.toBeInTheDocument();
  });

  it("keeps a draft until the saved policy or account changes", async () => {
    const user = userEvent.setup();
    const account = createAccountSummary({ usageLimitEnabled: true, usageLimitPercent: 90, usageLimitState: "available" });
    const { rerender } = render(control(account));
    const input = reserve("5-hour");

    await user.clear(input);
    await user.type(input, "87.5");
    rerender(control({ ...account, usageLimitState: "reached" }));
    expect(input).toHaveFocus();
    expect(input).toHaveValue(87.5);

    rerender(control({ ...account, usageLimitPercent: 80 }));
    expect(input).toHaveValue(20);

    await user.clear(input);
    await user.type(input, "70");
    rerender(control({ ...account, accountId: "acc_secondary" }));
    expect(input).toHaveValue(10);
  });

  it("ignores Enter on an unchanged draft, reverts with Escape, and disables controls while busy", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({ usageLimitEnabled: true, usageLimitPercent: 90, usageLimitState: "available" });
    const { rerender } = render(control(account, onChange));

    await user.type(reserve("5-hour"), "{Enter}");
    expect(onChange).not.toHaveBeenCalled();

    await user.type(reserve("5-hour"), "5{Escape}");
    expect(reserve("5-hour")).toHaveValue(10);
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();

    rerender(control(account, onChange, true));
    expect(screen.getByRole("switch", { name: "Protect reserved quota" })).toBeDisabled();
    expect(reserve("5-hour")).toBeDisabled();
  });

  it("explains invalid percentages and clears the error after correction", async () => {
    const user = userEvent.setup();
    render(control(createAccountSummary({ usageLimitPercent: null })));

    const input = reserve("5-hour");
    await user.type(input, "100");

    expect(input).toHaveAttribute("max", "99.99999999999999");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a reserve of at least 0% and below 100%.");
    expect(screen.getByRole("button", { name: "Save and enable" })).toBeDisabled();

    await user.clear(input);
    await user.type(input, "90");

    expect(input).toHaveAttribute("aria-invalid", "false");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save and enable" })).toBeEnabled();
  });

  it.each([
    { configured: 1e-7, edited: "99.9999998", saved: 2e-7 },
    { configured: 0.001, edited: "99.998", saved: 0.002 },
    { configured: 99.999, edited: "0.002", saved: 99.998 },
  ])("preserves and saves the configured precision for $configured percent", async ({ configured, edited, saved }) => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      usageLimitEnabled: true, usageLimitPercent: configured, usageLimitState: "available",
    });
    render(control(account, onChange));

    expect(reserve("Weekly")).toHaveValue(Number((100 - configured).toFixed(7)));
    await user.clear(reserve("5-hour"));
    await user.type(reserve("5-hour"), edited);
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, {
      enabled: true, percent5H: saved, percentWeekly: null,
    });
  });

  it("edits only the weekly override of a weekly-only account and keeps hidden saved values", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      planType: "pro", windowMinutesPrimary: null, windowMinutesSecondary: 10080,
      usage: { primaryRemainingPercent: null, secondaryRemainingPercent: 83 },
      usageLimitPercent: 80, usageLimit5HPercent: 60, usageLimitEnabled: true,
    });
    render(control(account, onChange));

    expect(screen.getAllByRole("spinbutton")).toHaveLength(1);
    expect(reserve("Weekly")).toHaveValue(20);
    await user.clear(reserve("Weekly"));
    await user.type(reserve("Weekly"), "30");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, { enabled: true, percentWeekly: 70 });
  });

  it("edits a monthly-only account through the default", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      planType: "free", windowMinutesPrimary: null, windowMinutesSecondary: null, windowMinutesMonthly: 43200,
      usage: { primaryRemainingPercent: null, secondaryRemainingPercent: null, monthlyRemainingPercent: 85 },
    });
    render(control(account, onChange));

    expect(screen.getAllByRole("spinbutton")).toHaveLength(1);
    await user.type(reserve("Monthly"), "20");
    expect(within(row("Monthly")).getByText("65%")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save and enable" }));

    expect(onChange).toHaveBeenCalledWith(account.accountId, { enabled: true, percent: 80 });
  });

  it("edits the default for windows without an override and keeps standard rows distinct", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const account = createAccountSummary({
      windowMinutesPrimary: 300, windowMinutesSecondary: 1440,
      usageLimitEnabled: true, usageLimitPercent: 80,
    });
    render(control(account, onChange));

    expect(reserve("5-hour")).toHaveValue(20);
    expect(reserve("Other windows")).toHaveValue(20);
    await user.clear(reserve("Other windows"));
    await user.type(reserve("Other windows"), "30");
    await user.clear(reserve("5-hour"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    // With a default in place, "no reserve" on a window needs an explicit full-use override.
    expect(onChange).toHaveBeenCalledWith(account.accountId, { enabled: true, percent: 70, percent5H: 100 });
  });

  it.each([
    [null, null, ["All windows"]],
    [60, 1440, ["All windows"]],
    [10080, null, ["Weekly"]],
    [300, 1440, ["5-hour", "Other windows"]],
  ])("offers rows for reported durations %s and %s", (primary, secondary, labels) => {
    render(control(createAccountSummary({
      windowMinutesPrimary: primary, windowMinutesSecondary: secondary, windowMinutesMonthly: null,
      usage: { primaryRemainingPercent: null, secondaryRemainingPercent: null },
    })));

    expect(screen.getAllByRole("spinbutton")).toHaveLength(labels.length);
    for (const label of labels) expect(reserve(label)).toBeInTheDocument();
  });
});
