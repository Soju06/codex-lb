import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { useDashboardPreferencesStore } from "@/hooks/use-dashboard-preferences";
import { usePrivacyStore } from "@/hooks/use-privacy";
import {
  createAccountSummary,
  createDashboardOverview,
  createRequestLogEntry,
  createRequestLogsResponse,
} from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

function trackRequests() {
  const urls: URL[] = [];
  server.events.on("request:start", ({ request }) => {
    urls.push(new URL(request.url));
  });
  return urls;
}

function requestLogCalls(urls: URL[]) {
  return urls.filter((url) => url.pathname === "/api/request-logs");
}

afterEach(() => {
  server.events.removeAllListeners();
  usePrivacyStore.setState({ blurred: false });
});

describe("DashboardOverviewPage", () => {
  it("loads only the overview and request-log endpoints and links back to the dashboard", async () => {
    const user = userEvent.setup({ delay: null });
    const urls = trackRequests();

    window.history.pushState({}, "", "/dashboard/overview");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(await screen.findAllByRole("row")).not.toHaveLength(0);
    const tabs = screen.getByRole("navigation", { name: "Dashboard views" });
    expect(within(tabs).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");

    const paths = new Set(urls.map((url) => url.pathname));
    expect(paths).toContain("/api/dashboard/overview");
    expect(paths).toContain("/api/request-logs");
    for (const heavy of ["/api/dashboard/projections", "/api/request-logs/options", "/api/conversations"]) {
      expect(paths).not.toContain(heavy);
    }
    expect(requestLogCalls(urls)[0]?.searchParams.get("limit")).toBe("25");
    expect(requestLogCalls(urls)[0]?.searchParams.get("offset")).toBe("0");

    await user.click(within(tabs).getByRole("link", { name: "Dashboard" }));
    await waitFor(() => expect(window.location.pathname).toBe("/dashboard"));
    const dashboardTabs = await screen.findByRole("navigation", { name: "Dashboard views" });
    expect(within(dashboardTabs).getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
    expect(within(dashboardTabs).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  it("writes the timeframe to the URL and refetches the overview for it", async () => {
    const user = userEvent.setup({ delay: null });
    const urls = trackRequests();

    window.history.pushState({}, "", "/dashboard/overview");
    renderWithProviders(<App />);

    const group = await screen.findByRole("group", { name: "Timeframe" });
    await user.click(within(group).getByRole("button", { name: "30d" }));

    await waitFor(() => expect(new URLSearchParams(window.location.search).get("overviewTimeframe")).toBe("30d"));
    await waitFor(() =>
      expect(urls.some((url) => url.pathname === "/api/dashboard/overview" && url.searchParams.get("timeframe") === "30d")).toBe(true),
    );
  });

  it("pages like the dashboard log and sends the status sets for each filter", async () => {
    const user = userEvent.setup({ delay: null });
    const urls = trackRequests();
    server.use(
      http.get("/api/request-logs", ({ request }) => {
        const params = new URL(request.url).searchParams;
        const limit = Number(params.get("limit"));
        const offset = Number(params.get("offset"));
        const rows = Array.from({ length: limit }, (_, index) => createRequestLogEntry({ requestId: `req_${offset + index}` }));
        return HttpResponse.json(createRequestLogsResponse(rows, 120, offset + limit < 120));
      }),
    );

    window.history.pushState({}, "", "/dashboard/overview");
    renderWithProviders(<App />);

    await user.click(await screen.findByRole("button", { name: "Next page" }));
    await waitFor(() => expect(requestLogCalls(urls).at(-1)?.searchParams.get("offset")).toBe("25"));

    const filter = screen.getByRole("group", { name: "Request status" });
    await user.click(within(filter).getByRole("button", { name: "Errors" }));
    await waitFor(() => {
      const last = requestLogCalls(urls).at(-1);
      expect(last?.searchParams.getAll("status")).toEqual(["error", "rate_limit", "quota", "cancelled"]);
      expect(last?.searchParams.get("offset")).toBe("0");
    });

    await user.click(within(filter).getByRole("button", { name: "Success" }));
    await waitFor(() => expect(requestLogCalls(urls).at(-1)?.searchParams.getAll("status")).toEqual(["ok"]));

    await user.click(within(filter).getByRole("button", { name: "All" }));
    await waitFor(() => expect(requestLogCalls(urls).at(-1)?.searchParams.has("status")).toBe(false));
  });

  it("narrows the account tiles to the selected plan and keeps it in the URL", async () => {
    const user = userEvent.setup({ delay: null });
    server.use(
      http.get("/api/dashboard/overview", () =>
        HttpResponse.json(
          createDashboardOverview({
            accounts: [
              createAccountSummary({ accountId: "plus_1", planType: "plus", status: "active" }),
              createAccountSummary({ accountId: "plus_2", planType: "plus", status: "rate_limited" }),
              createAccountSummary({ accountId: "pro_1", planType: "pro", status: "active" }),
            ],
          }),
        ),
      ),
    );

    window.history.pushState({}, "", "/dashboard/overview");
    renderWithProviders(<App />);

    expect(await screen.findByText("/ 3 active")).toBeInTheDocument();
    await user.click(screen.getByRole("combobox", { name: "Plan" }));
    await user.click(await screen.findByRole("option", { name: "Pro · 1" }));

    expect(await screen.findByText("/ 1 active")).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("plan")).toBe("pro");
  });

  it("shows the free plan's monthly quota on its own and leaves it out of the 5-hour count", async () => {
    server.use(
      http.get("/api/dashboard/overview", () =>
        HttpResponse.json(
          createDashboardOverview({
            accounts: [
              createAccountSummary({ accountId: "plus_1", planType: "plus" }),
              createAccountSummary({
                accountId: "free_1",
                planType: "free",
                usage: { primaryRemainingPercent: null, secondaryRemainingPercent: null, monthlyRemainingPercent: 30 },
                windowMinutesPrimary: null,
                windowMinutesSecondary: null,
                windowMinutesMonthly: 43_200,
                resetAtPrimary: null,
                resetAtSecondary: null,
              }),
            ],
          }),
        ),
      ),
    );

    window.history.pushState({}, "", "/dashboard/overview");
    const { unmount } = renderWithProviders(<App />);

    expect(await screen.findByText("Monthly")).toBeInTheDocument();
    expect(screen.getByText("5-hour quota")).toBeInTheDocument();
    expect(screen.queryByText("1/2 accounts with data")).not.toBeInTheDocument();
    unmount();

    window.history.pushState({}, "", "/dashboard/overview?plan=free");
    renderWithProviders(<App />);

    expect(await screen.findByText("Monthly")).toBeInTheDocument();
    expect(screen.queryByText("5-hour quota")).not.toBeInTheDocument();
    expect(screen.queryByText("Weekly")).not.toBeInTheDocument();
    expect(screen.getByText("70%")).toBeInTheDocument();
  });

  it("switches a large plan to distributions instead of one mark per account", async () => {
    const accounts = Array.from({ length: 101 }, (_, index) =>
      createAccountSummary({ accountId: `free_${index}`, planType: "free", email: `f${index}@example.com`, displayName: `f${index}@example.com` }),
    );
    server.use(http.get("/api/dashboard/overview", () => HttpResponse.json(createDashboardOverview({ accounts }))));

    window.history.pushState({}, "", "/dashboard/overview?plan=free");
    renderWithProviders(<App />);

    expect(await screen.findByTestId("status-distribution")).toBeInTheDocument();
    expect(screen.getAllByText("accounts by % used").length).toBeGreaterThan(0);
    expect(screen.queryByText("one column per account")).not.toBeInTheDocument();
  });

  it("stops polling the request list while auto refresh is off", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      useDashboardPreferencesStore.setState({ refreshSeconds: 5 });
      const user = userEvent.setup({ delay: null, advanceTimers: vi.advanceTimersByTime });
      const urls = trackRequests();

      window.history.pushState({}, "", "/dashboard/overview");
      renderWithProviders(<App />);

      await screen.findByRole("switch", { name: "Auto refresh" });
      await waitFor(() => expect(requestLogCalls(urls)).toHaveLength(1));
      await act(() => vi.advanceTimersByTimeAsync(5_500));
      await waitFor(() => expect(requestLogCalls(urls).length).toBeGreaterThanOrEqual(2));

      await user.click(screen.getByRole("switch", { name: "Auto refresh" }));
      const afterToggle = requestLogCalls(urls).length;
      await act(() => vi.advanceTimersByTimeAsync(16_000));
      expect(requestLogCalls(urls)).toHaveLength(afterToggle);
    } finally {
      vi.useRealTimers();
    }
  });

  it("blurs email account labels in privacy mode", async () => {
    usePrivacyStore.setState({ blurred: true });

    window.history.pushState({}, "", "/dashboard/overview");
    renderWithProviders(<App />);

    const table = await screen.findByRole("table");
    const label = await within(table).findAllByText("primary@example.com");
    expect(label[0]).toHaveClass("privacy-blur");
  });

  it("flags a failed refresh while keeping the last overview on screen", async () => {
    const user = userEvent.setup({ delay: null });

    window.history.pushState({}, "", "/dashboard/overview");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Activity over time" })).toBeInTheDocument();
    server.use(
      http.get("/api/dashboard/overview", () =>
        HttpResponse.json({ error: { code: "boom", message: "overview outage" } }, { status: 500 }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Refresh dashboard" }));

    expect(await screen.findByText("overview outage")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Activity over time" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("keeps activity and requests when the pool has no accounts", async () => {
    server.use(http.get("/api/dashboard/overview", () => HttpResponse.json(createDashboardOverview({ accounts: [] }))));

    window.history.pushState({}, "", "/dashboard/overview");
    renderWithProviders(<App />);

    expect(await screen.findByText("No accounts yet")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Activity over time" })).toBeInTheDocument();
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });
});
