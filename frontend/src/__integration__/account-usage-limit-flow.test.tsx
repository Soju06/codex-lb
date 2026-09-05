import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { createAccountSummary } from "@/test/mocks/factories";
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

function installUsageLimitUpdateHandler() {
  server.use(
    http.put("/api/accounts/:accountId/usage-limit", async ({ params, request }) => {
      const payload = (await request.json()) as {
        enabled: boolean;
        percent?: number | null;
      };
      return HttpResponse.json({
        accountId: String(params.accountId),
        ...payload,
      });
    }),
  );
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
    installUsageLimitUpdateHandler();
    const user = renderAccountsPage();

    const usageLimitSwitch = await screen.findByRole("switch", {
      name: "Usage limit",
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
    installUsageLimitUpdateHandler();
    const user = renderAccountsPage();

    const input = await screen.findByRole("spinbutton", {
      name: "Maximum used percent",
    });
    await user.clear(input);
    await user.type(input, "10");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(accountListRequests()).toBeGreaterThanOrEqual(2);
      expect(screen.getByText("10% maximum used · 90% reserved")).toBeInTheDocument();
      expect(screen.getByText("Usage unavailable · routing blocked")).toBeInTheDocument();
      expect(screen.getByText("Forced account-list outage")).toBeInTheDocument();
    });
  });

  it("disables from a stale tab without reverting the newer stored percentage", async () => {
    const user = userEvent.setup({ delay: null });
    const staleAccount = createAccountSummary({
      accountId: "acc-stale-usage-limit",
      email: "stale-usage-limit@example.com",
      displayName: "Stale Usage Limit Account",
      usageLimitEnabled: true,
      usageLimitPercent: 10,
      usageLimitState: "available",
    });
    let accountListRequests = 0;
    let storedPercent: number | null = 20;
    const updatePayloads: Array<{ enabled: boolean; percent?: number | null }> = [];

    server.use(
      http.get("/api/accounts", () => {
        accountListRequests += 1;
        return HttpResponse.json({
          accounts: [
            accountListRequests === 1
              ? staleAccount
              : {
                  ...staleAccount,
                  usageLimitEnabled: false,
                  usageLimitPercent: storedPercent,
                  usageLimitState: "disabled",
                },
          ],
        });
      }),
      http.put("/api/accounts/:accountId/usage-limit", async ({ params, request }) => {
        const payload = (await request.json()) as {
          enabled: boolean;
          percent?: number | null;
        };
        updatePayloads.push(payload);
        if (payload.percent !== undefined) {
          storedPercent = payload.percent;
        }
        return HttpResponse.json({
          accountId: String(params.accountId),
          enabled: payload.enabled,
          percent: storedPercent,
        });
      }),
    );

    window.history.pushState({}, "", "/accounts");
    renderWithProviders(<App />);

    const usageLimitSwitch = await screen.findByRole("switch", {
      name: "Usage limit",
    });
    expect(usageLimitSwitch).toBeChecked();
    expect(screen.getByText("10% maximum used · 90% reserved")).toBeInTheDocument();

    await user.click(usageLimitSwitch);

    await waitFor(() => {
      expect(updatePayloads).toEqual([{ enabled: false }]);
      expect(screen.getByRole("switch", { name: "Usage limit" })).not.toBeChecked();
      expect(screen.getByText("20% maximum used · 80% reserved")).toBeInTheDocument();
    });
  });
});
