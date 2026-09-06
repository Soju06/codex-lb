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

  it("does not carry an unresolved Retry into a new timeframe", async () => {
    const user = userEvent.setup({ delay: null });
    let retryPreviousTimeframe = false;
    let signalPreviousRetry = () => {};
    const previousRetryStarted = new Promise<void>((resolve) => {
      signalPreviousRetry = resolve;
    });
    let releasePreviousRetry = () => {};
    const previousRetryGate = new Promise<void>((resolve) => {
      releasePreviousRetry = resolve;
    });
    let signalNextTimeframeRequest = () => {};
    const nextTimeframeRequestStarted = new Promise<void>((resolve) => {
      signalNextTimeframeRequest = resolve;
    });
    let releaseNextTimeframeRequest = () => {};
    const nextTimeframeRequestGate = new Promise<void>((resolve) => {
      releaseNextTimeframeRequest = resolve;
    });

    server.use(
      http.get("/api/dashboard/overview", async ({ request }) => {
        const timeframe = new URL(request.url).searchParams.get("timeframe");
        if (timeframe === "30d") {
          signalNextTimeframeRequest();
          await nextTimeframeRequestGate;
          return HttpResponse.json(
            createDashboardOverview({
              timeframe: {
                key: "30d",
                windowMinutes: 43_200,
                bucketSeconds: 86_400,
                bucketCount: 30,
              },
              accounts: [
                createAccountSummary({
                  accountId: "acc_thirty_day",
                  chatgptAccountId: "chatgpt_acc_thirty_day",
                  displayName: "Thirty Day Overview Account",
                  email: "thirty-day@example.com",
                }),
              ],
            }),
          );
        }
        if (!retryPreviousTimeframe) {
          return HttpResponse.json(
            { error: { code: "forced_outage", message: OUTAGE } },
            { status: 503 },
          );
        }
        signalPreviousRetry();
        await previousRetryGate;
        return HttpResponse.json(createDashboardOverview());
      }),
    );

    window.history.pushState({}, "", "/dashboard");
    const { container, queryClient } = renderWithProviders(<App />);

    expect(await screen.findByText(OUTAGE)).toBeInTheDocument();
    retryPreviousTimeframe = true;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await previousRetryStarted;

    await user.click(screen.getByRole("combobox", { name: /timeframe/i }));
    await user.click(screen.getByRole("option", { name: "30d" }));
    await nextTimeframeRequestStarted;

    expect(screen.queryByText(OUTAGE)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);

    const previousRetrySettled = new Promise<void>((resolve) => {
      const unsubscribe = queryClient.getQueryCache().subscribe((event) => {
        const queryKey = event.query.queryKey;
        if (
          queryKey[0] === "dashboard" &&
          queryKey[1] === "overview" &&
          queryKey[2] === "7d" &&
          event.query.state.fetchStatus === "idle"
        ) {
          unsubscribe();
          resolve();
        }
      });
    });
    releasePreviousRetry();
    await previousRetrySettled;
    expect(screen.queryByText(OUTAGE)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();

    releaseNextTimeframeRequest();
    expect((await screen.findAllByText("Thirty Day Overview Account")).length).toBeGreaterThan(0);
  });

  it("retains terminal no-data error during an invalidation refetch", async () => {
    const user = userEvent.setup({ delay: null });
    let recoverOnRefresh = false;
    let signalRefresh = () => {};
    const refreshStarted = new Promise<void>((resolve) => {
      signalRefresh = resolve;
    });
    let releaseRefresh = () => {};
    const refreshGate = new Promise<void>((resolve) => {
      releaseRefresh = resolve;
    });

    server.use(
      http.get("/api/dashboard/overview", async () => {
        if (!recoverOnRefresh) {
          return HttpResponse.json(
            { error: { code: "forced_outage", message: OUTAGE } },
            { status: 503 },
          );
        }
        signalRefresh();
        await refreshGate;
        return HttpResponse.json(
          createDashboardOverview({
            accounts: [
              createAccountSummary({
                accountId: "acc_refreshed",
                chatgptAccountId: "chatgpt_acc_refreshed",
                displayName: RECOVERED,
                email: "refreshed@example.com",
              }),
            ],
          }),
        );
      }),
    );

    window.history.pushState({}, "", "/dashboard");
    const { container } = renderWithProviders(<App />);

    expect(await screen.findByText(OUTAGE)).toBeInTheDocument();
    recoverOnRefresh = true;
    await user.click(screen.getByRole("button", { name: "Refresh dashboard" }));
    await refreshStarted;

    const alert = screen.getByRole("alert");
    const terminalState = alert.parentElement ?? alert;
    const retry = within(terminalState).getByRole("button", { name: "Retry" });
    expect(alert).toHaveTextContent(OUTAGE);
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(0);
    expect(retry).toBeDisabled();
    expect(retry).toHaveAttribute("aria-busy", "true");

    releaseRefresh();
    expect((await screen.findAllByText(RECOVERED)).length).toBeGreaterThan(0);
  });
});
