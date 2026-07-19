import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { BrowserRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import App from "@/App";
import {
  createAccountSummary,
  createDashboardOverview,
  createDashboardProjections,
  createDefaultRequestLogs,
  createRequestLogEntry,
  createRequestLogFilterOptions,
  createRequestLogsResponse,
} from "@/test/mocks/factories";
import { queryClient } from "@/lib/query-client";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

if (!HTMLElement.prototype.scrollIntoView) {
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: () => {},
  });
}

const REQUEST_LOG_OUTAGE_MESSAGE = "forced request-log outage";

afterEach(() => {
  queryClient.clear();
});

describe("dashboard flow integration", () => {
  it("loads dashboard, refetches overview on overview-timeframe changes, and keeps request-log refetches isolated", async () => {
    const user = userEvent.setup({ delay: null });
    const logs = createDefaultRequestLogs();

    let overviewCalls = 0;
    let requestLogCalls = 0;
    const overviewTimeframes: string[] = [];

    server.use(
      http.get("/api/dashboard/overview", ({ request }) => {
        overviewCalls += 1;
        const timeframe = (new URL(request.url).searchParams.get("timeframe") ?? "7d") as "1d" | "7d" | "30d";
        overviewTimeframes.push(timeframe);
        return HttpResponse.json(createDashboardOverview({
          timeframe:
            timeframe === "1d"
              ? { key: "1d", windowMinutes: 1440, bucketSeconds: 3600, bucketCount: 24 }
              : timeframe === "30d"
                ? { key: "30d", windowMinutes: 43200, bucketSeconds: 86400, bucketCount: 30 }
                : { key: "7d", windowMinutes: 10080, bucketSeconds: 21600, bucketCount: 28 },
        }));
      }),
      http.get("/api/request-logs", ({ request }) => {
        requestLogCalls += 1;
        const url = new URL(request.url);
        const limit = Number(url.searchParams.get("limit") ?? "25");
        const offset = Number(url.searchParams.get("offset") ?? "0");
        const page = logs.slice(offset, Math.min(logs.length, offset + limit));
        return HttpResponse.json(createRequestLogsResponse(page, 100, true));
      }),
      http.get("/api/request-logs/options", () =>
        HttpResponse.json(createRequestLogFilterOptions()),
      ),
    );

    window.history.pushState({}, "", "/dashboard");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(await screen.findByText("Request Logs")).toBeInTheDocument();

    await waitFor(() => {
      expect(overviewCalls).toBeGreaterThan(0);
      expect(requestLogCalls).toBeGreaterThan(0);
    });

    const overviewAfterLoad = overviewCalls;
    const logsAfterLoad = requestLogCalls;
    expect(overviewTimeframes.at(-1)).toBe("7d");

    act(() => {
      window.history.pushState({}, "", "/dashboard?overviewTimeframe=30d");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    await waitFor(() => {
      expect(overviewCalls).toBeGreaterThan(overviewAfterLoad);
    });
    expect(requestLogCalls).toBe(logsAfterLoad);
    expect(overviewTimeframes.at(-1)).toBe("30d");

    const overviewAfterTimeframe = overviewCalls;

    await user.type(
      screen.getByPlaceholderText("Search request id, account, API key, model, error..."),
      "quota",
    );

    await waitFor(() => {
      expect(requestLogCalls).toBeGreaterThan(logsAfterLoad);
    });
    expect(overviewCalls).toBe(overviewAfterTimeframe);

    const logsAfterFilter = requestLogCalls;
    await user.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() => {
      expect(requestLogCalls).toBeGreaterThan(logsAfterFilter);
    });
    expect(overviewCalls).toBe(overviewAfterTimeframe);
  });

  it("preserves healthy overview through initial request-log failure and Retry recovery", async () => {
    const user = userEvent.setup({ delay: null });
    let overviewCalls = 0;
    let projectionsCalls = 0;
    let requestLogCalls = 0;
    let optionsCalls = 0;
    let requestLogsAvailable = false;
    let releaseRecoveredResponse = () => {};
    const recoveredResponseGate = new Promise<void>((resolve) => {
      releaseRecoveredResponse = resolve;
    });
    const recoveredLog = createRequestLogEntry({
      requestId: "req_recovered",
      accountId: "acc_healthy_overview",
      apiKeyName: "Recovered API Key",
    });

    const healthyAccount = createAccountSummary({
      accountId: "acc_healthy_overview",
      chatgptAccountId: "chatgpt_acc_healthy_overview",
      email: "healthy-overview@example.com",
      displayName: "Healthy Overview Account",
      usage: {
        primaryRemainingPercent: 61.3,
        secondaryRemainingPercent: 88.3,
        monthlyRemainingPercent: null,
      },
      capacityCreditsPrimary: 9_876,
      remainingCreditsPrimary: 6_055,
      remainingCreditsSecondary: 6_675.48,
    });
    const baseOverview = createDashboardOverview({ accounts: [healthyAccount] });
    const overview = createDashboardOverview({
      accounts: [healthyAccount],
      summary: {
        ...baseOverview.summary,
        primaryWindow: {
          ...baseOverview.summary.primaryWindow,
          remainingPercent: 61.3,
          capacityCredits: 9_876,
          remainingCredits: 6_055,
        },
        metrics: {
          ...baseOverview.summary.metrics!,
          requests: 424_242,
        },
      },
      windows: {
        ...baseOverview.windows,
        primary: {
          ...baseOverview.windows.primary,
          accounts: [
            {
              accountId: healthyAccount.accountId,
              remainingPercentAvg: 61.3,
              capacityCredits: 9_876,
              remainingCredits: 6_055,
            },
          ],
        },
      },
    });

    server.use(
      http.get("/api/dashboard/overview", () => {
        overviewCalls += 1;
        return HttpResponse.json(overview);
      }),
      http.get("/api/dashboard/projections", () => {
        projectionsCalls += 1;
        return HttpResponse.json(createDashboardProjections());
      }),
      http.get("/api/request-logs/options", () => {
        optionsCalls += 1;
        return HttpResponse.json(createRequestLogFilterOptions());
      }),
      http.get("/api/request-logs", async () => {
        requestLogCalls += 1;
        if (!requestLogsAvailable) {
          return HttpResponse.json(
            {
              error: {
                code: "forced_request_log_outage",
                message: REQUEST_LOG_OUTAGE_MESSAGE,
              },
            },
            { status: 500 },
          );
        }

        await recoveredResponseGate;
        return HttpResponse.json(createRequestLogsResponse([recoveredLog], 1, false));
      }),
    );

    window.history.pushState({}, "", "/dashboard");
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    const requestLogsHeading = await screen.findByRole("heading", { name: "Request Logs" });
    const requestLogsSection = requestLogsHeading.closest("section");

    expect(requestLogsSection).not.toBeNull();
    const requestLogs = within(requestLogsSection as HTMLElement);
    const errorAlert = await requestLogs.findByRole("alert");

    await waitFor(() => {
      expect(overviewCalls).toBeGreaterThan(0);
      expect(projectionsCalls).toBeGreaterThan(0);
      expect(requestLogCalls).toBeGreaterThan(1);
      expect(optionsCalls).toBeGreaterThan(0);
    });

    const expectHealthySurfaces = () => {
      expect(screen.getByText("Requests (7d)")).toBeInTheDocument();
      expect(screen.getByText("424.24K")).toBeInTheDocument();
      expect(screen.getByText("Account burn projection (5h/7d)")).toBeInTheDocument();
      expect(screen.getByText("0.4 / 0.1")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "5-Hour Credits" })).toBeInTheDocument();
      expect(screen.getByText("6,055")).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Enable limit warm-up for Healthy Overview Account" }),
      ).toBeInTheDocument();
    };

    await waitFor(expectHealthySurfaces);
    expect(screen.getByRole("heading", { name: "Accounts" })).toBeInTheDocument();
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(0);
    expect(errorAlert).toHaveTextContent(REQUEST_LOG_OUTAGE_MESSAGE);
    expect(requestLogs.getByRole("button", { name: "Retry" })).toBeInTheDocument();

    const overviewCallsBeforeRetry = overviewCalls;
    const projectionsCallsBeforeRetry = projectionsCalls;
    const requestLogCallsBeforeRetry = requestLogCalls;
    const optionsCallsBeforeRetry = optionsCalls;
    const retryButton = requestLogs.getByRole("button", { name: "Retry" });
    requestLogsAvailable = true;

    retryButton.focus();
    expect(retryButton).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(requestLogCalls).toBe(requestLogCallsBeforeRetry + 1);
    });

    expectHealthySurfaces();
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(0);
    expect(overviewCalls).toBe(overviewCallsBeforeRetry);
    expect(projectionsCalls).toBe(projectionsCallsBeforeRetry);
    expect(optionsCalls).toBe(optionsCallsBeforeRetry);

    releaseRecoveredResponse();
    expect(await screen.findByText("Recovered API Key")).toBeInTheDocument();
    expect(screen.queryByText(REQUEST_LOG_OUTAGE_MESSAGE)).not.toBeInTheDocument();
    expectHealthySurfaces();
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(0);
    expect(overviewCalls).toBe(overviewCallsBeforeRetry);
    expect(projectionsCalls).toBe(projectionsCallsBeforeRetry);
    expect(optionsCalls).toBe(optionsCallsBeforeRetry);
  });
});
