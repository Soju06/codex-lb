import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { createAccountSummary } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

import { AccountMultiSelect } from "./account-multi-select";

describe("AccountMultiSelect", () => {
  it("shows available account limits inside the picker", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({
              accountId: "acc_quota",
              email: "quota@example.com",
              displayName: "Quota account",
              usage: {
                primaryRemainingPercent: 82,
                secondaryRemainingPercent: 67,
              },
            }),
          ],
        }),
      ),
    );

    const user = userEvent.setup();

    renderWithProviders(<AccountMultiSelect value={[]} onChange={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "All accounts" }));

    expect(await screen.findByText("5h 82% left")).toBeInTheDocument();
    expect(screen.getByText("7d 67% left")).toBeInTheDocument();
    const quotaOption = screen.getByRole("menuitemcheckbox", { name: /quota@example\.com/i });
    expect(quotaOption).toHaveClass("[&>span:first-child]:top-[11px]");
    expect(quotaOption).toHaveClass("[&>span:first-child]:translate-y-0");
    expect(screen.queryByText(/GPT-5\.3-Codex-Spark/i)).not.toBeInTheDocument();
  });

  it("keeps account selection working with the richer rows", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    renderWithProviders(<AccountMultiSelect value={[]} onChange={onChange} />);

    await user.click(await screen.findByRole("button", { name: "All accounts" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: /primary@example\.com/i }));

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith(["acc_primary"]);
    });
  });

  it("keeps an empty explicit selection empty and hides the all-accounts choice", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    renderWithProviders(
      <AccountMultiSelect
        value={[]}
        onChange={onChange}
        selectionMode="explicit"
        placeholder="Select allowed accounts"
      />,
    );

    await user.click(await screen.findByRole("button", { name: "Select allowed accounts" }));

    expect(screen.queryByRole("menuitemcheckbox", { name: "All accounts" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitemcheckbox", { name: /primary@example\.com/i }));
    expect(onChange).toHaveBeenCalledWith(["acc_primary"]);
  });

  it("renders a compact email-only list with right-aligned selection marks", async () => {
    const user = userEvent.setup();

    renderWithProviders(
      <AccountMultiSelect
        value={["acc_primary"]}
        onChange={vi.fn()}
        selectionMode="explicit"
        presentation="compact"
      />,
    );

    await user.click(await screen.findByRole("button", { name: "1 account selected" }));

    const option = screen.getByRole("menuitemcheckbox", { name: "primary@example.com" });
    expect(option).toHaveClass("[&>span:first-child]:right-2");
    expect(option).toHaveClass("[&>span:first-child]:top-1/2");
    expect(screen.queryByText("Pro")).not.toBeInTheDocument();
    expect(screen.queryByText(/left$/i)).not.toBeInTheDocument();
    expect(screen.queryByText("primary@example.com", { selector: "[data-slot='badge']" })).not.toBeInTheDocument();
  });

  it("pluralizes the selected account count", async () => {
    renderWithProviders(
      <AccountMultiSelect value={["acc_primary", "acc_secondary"]} onChange={vi.fn()} />,
    );

    expect(await screen.findByRole("button", { name: "2 accounts selected" })).toBeInTheDocument();
  });

  it("keeps selected hard-blocked accounts removable while hiding unselected ones", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({
              accountId: "acc_active_picker",
              email: "active-picker@example.com",
              displayName: "Active picker",
            }),
            createAccountSummary({
              accountId: "acc_reauth_picker",
              email: "reauth-picker@example.com",
              displayName: "Reauth picker",
              status: "reauth_required",
            }),
            createAccountSummary({
              accountId: "acc_paused_picker",
              email: "paused-picker@example.com",
              displayName: "Paused picker",
              status: "paused",
            }),
            createAccountSummary({
              accountId: "acc_deactivated_picker",
              email: "deactivated-picker@example.com",
              displayName: "Deactivated picker",
              status: "deactivated",
            }),
          ],
        }),
      ),
    );

    const user = userEvent.setup();
    const onChange = vi.fn();

    renderWithProviders(
      <AccountMultiSelect
        value={["acc_reauth_picker"]}
        onChange={onChange}
        selectionMode="explicit"
        presentation="compact"
      />,
    );

    await user.click(await screen.findByRole("button", { name: "1 account selected" }));

    expect(await screen.findByRole("menuitemcheckbox", { name: /active-picker@example\.com/i })).toBeInTheDocument();
    const selectedUnavailable = screen.getByRole("menuitemcheckbox", { name: /reauth-picker@example\.com/i });
    expect(selectedUnavailable).toBeChecked();
    expect(screen.queryByRole("menuitemcheckbox", { name: /paused-picker@example\.com/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitemcheckbox", { name: /deactivated-picker@example\.com/i })).not.toBeInTheDocument();

    await user.click(selectedUnavailable);
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("can include paused accounts while keeping other hard-blocked accounts hidden", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({
              accountId: "acc_paused_picker",
              email: "paused-picker@example.com",
              displayName: "Paused picker",
              status: "paused",
            }),
            createAccountSummary({
              accountId: "acc_reauth_picker",
              email: "reauth-picker@example.com",
              displayName: "Reauth picker",
              status: "reauth_required",
            }),
            createAccountSummary({
              accountId: "acc_deactivated_picker",
              email: "deactivated-picker@example.com",
              displayName: "Deactivated picker",
              status: "deactivated",
            }),
          ],
        }),
      ),
    );

    const user = userEvent.setup();

    renderWithProviders(<AccountMultiSelect value={[]} onChange={vi.fn()} allowPausedAccounts />);

    await user.click(await screen.findByRole("button", { name: "All accounts" }));

    expect(await screen.findByRole("menuitemcheckbox", { name: /paused-picker@example\.com/i })).toBeInTheDocument();
    expect(screen.queryByRole("menuitemcheckbox", { name: /reauth-picker@example\.com/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitemcheckbox", { name: /deactivated-picker@example\.com/i })).not.toBeInTheDocument();
  });

  it("shows Monthly left for monthly-only free accounts", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({
              accountId: "acc_free",
              email: "free@example.com",
              displayName: "Free monthly",
              planType: "free",
              usage: {
                primaryRemainingPercent: null,
                secondaryRemainingPercent: null,
                monthlyRemainingPercent: 95,
              },
              windowMinutesPrimary: null,
              windowMinutesSecondary: null,
              windowMinutesMonthly: 43_200,
            }),
          ],
        }),
      ),
    );

    const user = userEvent.setup();

    renderWithProviders(<AccountMultiSelect value={[]} onChange={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "All accounts" }));

    expect(await screen.findByText("Monthly 95% left")).toBeInTheDocument();
    expect(screen.queryByText(/7d .* left/i)).not.toBeInTheDocument();
  });
});
