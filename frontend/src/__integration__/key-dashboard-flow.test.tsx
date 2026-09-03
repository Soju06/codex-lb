import { HttpResponse, http } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import App from "@/App";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

const TEST_KEY = "sk-clb-key-dashboard-secret";

const safeLog = {
  requestedAt: "2026-09-03T12:00:00Z",
  requestId: "req-key-dashboard-1",
  requestKind: "normal",
  model: "gpt-5.1",
  transport: "http",
  upstreamTransport: "websocket",
  serviceTier: null,
  requestedServiceTier: null,
  actualServiceTier: null,
  reasoningEffort: "medium",
  status: "ok",
  errorCode: null,
  tokens: 125,
  inputTokens: 100,
  outputTokens: 25,
  outputTokensRaw: 25,
  reasoningTokens: 5,
  cachedInputTokens: 20,
  costUsd: 0.0123,
  costBreakdown: {
    inputUsd: 0.004,
    cachedInputUsd: 0.001,
    outputUsd: 0.0073,
    totalUsd: 0.0123,
  },
  latencyMs: 500,
  latencyFirstTokenMs: 100,
  latencyQueueMs: 20,
};

describe("API key dashboard integration", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/key-dashboard");
    window.localStorage.removeItem(TEST_KEY);
  });

  it("bypasses administrator auth and loads only key-scoped safe data", async () => {
    const seenPaths: string[] = [];
    const credentials: RequestCredentials[] = [];
    const logOffsets: string[] = [];
    let usageCalls = 0;
    server.use(
      http.get("/api/dashboard-auth/session", () => {
        seenPaths.push("/api/dashboard-auth/session");
        return HttpResponse.json({ error: { code: "unexpected", message: "Unexpected admin auth" } }, { status: 500 });
      }),
      http.get("/v1/usage", ({ request }) => {
        seenPaths.push("/v1/usage");
        credentials.push(request.credentials);
        usageCalls += 1;
        expect(request.headers.get("Authorization")).toBe(`Bearer ${TEST_KEY}`);
        return HttpResponse.json({
          request_count: 9,
          total_tokens: 12_500,
          cached_input_tokens: 2_500,
          total_cost_usd: 0.42,
          limits: [],
          upstream_limits: [],
          account_pool_usage: null,
        });
      }),
      http.get("/api/key-dashboard/request-logs", ({ request }) => {
        seenPaths.push("/api/key-dashboard/request-logs");
        credentials.push(request.credentials);
        expect(request.headers.get("Authorization")).toBe(`Bearer ${TEST_KEY}`);
        const offset = new URL(request.url).searchParams.get("offset") ?? "0";
        logOffsets.push(offset);
        return HttpResponse.json({
          requests: [{ ...safeLog, requestId: offset === "0" ? safeLog.requestId : "req-key-dashboard-page-2" }],
          total: 26,
          hasMore: offset === "0",
        });
      }),
    );
    const user = userEvent.setup();

    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "View your API key usage" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Open dashboard" }));

    expect(await screen.findByRole("heading", { name: "API key dashboard" })).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "View Details" }));
    expect(await screen.findByText("req-key-dashboard-1")).toBeInTheDocument();
    expect(screen.queryByText("User agent")).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.getByText("12.5K")).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Account" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "API Key" })).not.toBeInTheDocument();
    expect(seenPaths).toEqual(expect.arrayContaining(["/v1/usage", "/api/key-dashboard/request-logs"]));
    expect(seenPaths).not.toContain("/api/dashboard-auth/session");
    expect(credentials).toEqual(["omit", "omit"]);
    expect(window.location.pathname).toBe("/key-dashboard");
    expect(window.location.search).toBe("");
    expect(Object.values(window.localStorage)).not.toContain(TEST_KEY);

    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(usageCalls).toBe(2));
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(logOffsets).toEqual(["0", "0", "25"]));
    expect(credentials).toEqual(["omit", "omit", "omit", "omit", "omit", "omit"]);
    expect(window.location.search).toBe("");

    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByRole("heading", { name: "View your API key usage" })).toBeInTheDocument();
    expect(screen.getByLabelText("API key")).toHaveValue("");
  });

  it("returns to key entry with an independent invalid-key error", async () => {
    server.use(
      http.get("/v1/usage", () =>
        HttpResponse.json(
          { error: { code: "invalid_api_key", message: "Invalid API key" } },
          { status: 401 },
        ),
      ),
      http.get("/api/key-dashboard/request-logs", () =>
        HttpResponse.json(
          { error: { code: "invalid_api_key", message: "Invalid API key" } },
          { status: 401 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<App />);

    await user.type(await screen.findByLabelText("API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Open dashboard" }));

    expect(await screen.findByText("This API key is invalid, inactive, or expired.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("API key")).toHaveValue(""));
  });
});
