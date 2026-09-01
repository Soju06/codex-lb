import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import App from "@/App";
import {
  createAccountSummary,
  createDashboardOverview,
} from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

const OUTAGE = "forced overview outage";
const RECOVERED = "Recovered Overview Account";

afterEach(() => {
  window.history.pushState({}, "", "/");
});

describe("dashboard overview error integration", () => {
  it("replaces terminal failure with announced Retry and recovers in place", async () => {
    const user = userEvent.setup({ delay: null });
    let overviewCalls = 0;
    let overviewAvailable = false;
    let signalRetry = () => {};
    const retryRequest = new Promise<void>((resolve) => {
      signalRetry = resolve;
    });
    let releaseRecovery = () => {};
    const recoveryGate = new Promise<void>((resolve) => {
      releaseRecovery = resolve;
    });

    server.use(
      http.get("/api/dashboard/overview", async () => {
        overviewCalls += 1;
        if (!overviewAvailable) {
          return HttpResponse.json(
            { error: { code: "forced_outage", message: OUTAGE } },
            { status: 503 },
          );
        }
        signalRetry();
        await recoveryGate;
        return HttpResponse.json(
          createDashboardOverview({
            accounts: [
              createAccountSummary({
                accountId: "acc_recovered_overview",
                chatgptAccountId: "chatgpt_acc_recovered_overview",
                displayName: RECOVERED,
                email: "recovered-overview@example.com",
              }),
            ],
          }),
        );
      }),
    );

    window.history.pushState({}, "", "/dashboard");
    const { container } = renderWithProviders(<App />);

    expect(await screen.findByText(OUTAGE)).toBeInTheDocument();
    await waitFor(() => expect(overviewCalls).toBeGreaterThan(1));

    const alert = screen.getByRole("alert");
    const terminalState = alert.parentElement ?? alert;
    const retry = within(terminalState).getByRole("button");
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(0);
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();

    overviewAvailable = true;
    retry.focus();
    await user.keyboard("{Enter}");
    await retryRequest;

    await waitFor(() => expect(retry).toBeDisabled());
    expect(retry).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("alert")).toHaveTextContent(OUTAGE);
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(0);

    releaseRecovery();
    expect(await screen.findByText(RECOVERED)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(overviewCalls).toBeGreaterThan(2);
  });
});
