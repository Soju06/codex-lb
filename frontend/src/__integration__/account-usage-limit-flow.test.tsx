import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { createAccountSummary } from "@/test/mocks/factories";
import { resetMockState } from "@/test/mocks/handlers";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

function installAccountRefetchFailure(account: ReturnType<typeof createAccountSummary>) {
  let requestCount = 0;
  server.use(
    http.get("/api/accounts", () => {
      requestCount += 1;
      if (requestCount === 1) {
        return HttpResponse.json({ accounts: [account] });
      }
      return HttpResponse.json(
        {
          error: {
            code: "forced_accounts_outage",
            message: "Forced account-list outage",
          },
        },
        { status: 500 },
      );
    }),
  );
  return () => requestCount;
}

function renderAccountsPage() {
  window.history.pushState({}, "", "/accounts");
  renderWithProviders(<App />);
  return userEvent.setup({ delay: null });
}

describe("account usage limit flow", () => {
  it("shows a successful limit update when the account-list refetch fails", async () => {
    const account = createAccountSummary({
      accountId: "acc-usage-limit",
      email: "usage-limit@example.com",
      displayName: "Usage Limit Account",
      usageLimitEnabled: false,
      usageLimitPercent: 10,
      usageLimitState: "disabled",
    });
    const accountListRequests = installAccountRefetchFailure(account);
    resetMockState([account]);
    const user = renderAccountsPage();

    const usageLimitSwitch = await screen.findByRole("switch", {
      name: "Protect reserved quota",
    });
    expect(usageLimitSwitch).not.toBeChecked();

    await user.click(usageLimitSwitch);

    await waitFor(() => {
      expect(accountListRequests()).toBeGreaterThanOrEqual(2);
      expect(usageLimitSwitch).toBeChecked();
      expect(screen.getByText("Usage unavailable · routing blocked")).toBeInTheDocument();
      expect(screen.getByText("Forced account-list outage")).toBeInTheDocument();
    });
  });

  it("does not preserve Active after lowering the limit when refetch fails", async () => {
    const account = createAccountSummary({
      accountId: "acc-lowered-usage-limit",
      email: "lowered-usage-limit@example.com",
      displayName: "Lowered Usage Limit Account",
      usageLimitEnabled: true,
      usageLimitPercent: 50,
      usageLimitState: "available",
    });
    const accountListRequests = installAccountRefetchFailure(account);
    resetMockState([account]);
    const user = renderAccountsPage();

    const input = await screen.findByRole("spinbutton", {
      name: "Reserve for yourself (%)",
    });
    await user.clear(input);
    await user.type(input, "90");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(accountListRequests()).toBeGreaterThanOrEqual(2);
      expect(screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" })).toHaveValue(90);
      expect(screen.getByText("Usage unavailable · routing blocked")).toBeInTheDocument();
      expect(screen.getByText("Forced account-list outage")).toBeInTheDocument();
    });
  });

  it.each([false, true])("toggles to %s from a stale tab without reverting newer thresholds", async (enabled) => {
    const staleAccount = createAccountSummary({
      accountId: "acc-stale-usage-limit",
      usageLimitEnabled: !enabled,
      usageLimitPercent: 10,
      usageLimit5HPercent: 30,
      usageLimitWeeklyPercent: 50,
      usageLimitState: enabled ? "disabled" : "available",
    });
    resetMockState([{
      ...staleAccount,
      usageLimitPercent: 20,
      usageLimit5HPercent: 40,
      usageLimitWeeklyPercent: 60,
    }]);
    let initial = true;
    server.use(http.get("/api/accounts", () => {
      if (!initial) return;
      initial = false;
      return HttpResponse.json({ accounts: [staleAccount] });
    }));
    const user = renderAccountsPage();
    const toggle = await screen.findByRole("switch", { name: "Protect reserved quota" });
    await user.click(toggle);

    await waitFor(() => {
      expect(toggle).toHaveAttribute("aria-checked", String(enabled));
      expect(screen.getByRole("spinbutton", { name: "Reserve for yourself (%)" })).toHaveValue(80);
      expect(screen.getByRole("spinbutton", { name: "5-hour reserve (%)" })).toHaveValue(60);
      expect(screen.getByRole("spinbutton", { name: "Weekly reserve (%)" })).toHaveValue(40);
    });
  });

  it("does not recreate a policy removed since the tab loaded", async () => {
    const account = createAccountSummary({ usageLimitEnabled: false, usageLimitPercent: 10 });
    resetMockState([{ ...account, usageLimitPercent: null }]);
    server.use(http.get("/api/accounts", () => HttpResponse.json({ accounts: [account] })));
    const user = renderAccountsPage();
    await user.click(await screen.findByRole("switch", { name: "Protect reserved quota" }));
    expect(await screen.findByText("Invalid account usage limit payload")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Protect reserved quota" })).not.toBeChecked();
  });
});
