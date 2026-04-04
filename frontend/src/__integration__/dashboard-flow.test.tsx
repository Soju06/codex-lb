import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import App from "@/App";
import {
  createDashboardOverview,
  createDefaultRequestLogs,
  createRequestLogFilterOptions,
  createRequestLogsResponse,
} from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

if (!HTMLElement.prototype.scrollIntoView) {
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: () => {},
  });
}

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
      screen.getByPlaceholderText("Search request id, account, model, error..."),
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
});
