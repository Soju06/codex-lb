import { HttpResponse, http } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import App from "@/App";
import { KEY_DASHBOARD_API_KEY_STORAGE_KEY } from "@/features/key-dashboard/storage";
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

const safeProfile = {
  name: "Production client",
  keyPrefix: "sk-clb-key-dash…",
  isActive: true,
  createdAt: "2026-08-01T08:00:00Z",
  expiresAt: "2027-08-01T08:00:00Z",
  lastUsedAt: "2026-09-03T11:59:00Z",
  allowedModels: ["gpt-5.1", "gpt-5.2"],
  enforcedModel: null,
  allowedReasoningEfforts: ["low", "medium"],
  enforcedReasoningEffort: null,
  enforcedServiceTier: "priority",
  trafficClass: "foreground",
  transportPolicyOverride: "always_websocket",
};

const usagePayload = {
  request_count: 9,
  total_tokens: 12_500,
  cached_input_tokens: 2_500,
  total_cost_usd: 0.42,
  limits: [{
    limit_type: "total_tokens",
    limit_window: "weekly",
    max_value: 50_000,
    current_value: 12_500,
    remaining_value: 37_500,
    model_filter: "gpt-5.1",
    reset_at: "2026-09-10T08:00:00Z",
    source: "api_key_limit",
  }],
  upstream_limits: [],
  account_pool_usage: null,
};

describe("API key dashboard integration", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/key-dashboard");
    window.localStorage.removeItem(KEY_DASHBOARD_API_KEY_STORAGE_KEY);
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
        return HttpResponse.json(usagePayload);
      }),
      http.get("/api/key-dashboard/profile", ({ request }) => {
        seenPaths.push("/api/key-dashboard/profile");
        credentials.push(request.credentials);
        expect(request.headers.get("Authorization")).toBe(`Bearer ${TEST_KEY}`);
        return HttpResponse.json(safeProfile);
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
    expect(screen.getByRole("heading", { name: "Production client" })).toBeInTheDocument();
    expect(screen.getByText("sk-clb-key-dash…")).toBeInTheDocument();
    expect(screen.getByText("Usage limits")).toBeInTheDocument();
    expect(screen.getByText("37.5K remaining")).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Account" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "API Key" })).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Time" })).toHaveStyle({ width: "136px" });
    expect(seenPaths).toEqual(expect.arrayContaining([
      "/api/key-dashboard/profile",
      "/v1/usage",
      "/api/key-dashboard/request-logs",
    ]));
    expect(seenPaths).not.toContain("/api/dashboard-auth/session");
    expect(credentials).toHaveLength(3);
    expect(credentials.every((credential) => credential === "omit")).toBe(true);
    expect(window.location.pathname).toBe("/key-dashboard");
    expect(window.location.search).toBe("");
    expect(window.localStorage.getItem(KEY_DASHBOARD_API_KEY_STORAGE_KEY)).toBeNull();

    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(usageCalls).toBe(2));
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(logOffsets).toEqual(["0", "0", "25"]));
    expect(credentials).toHaveLength(9);
    expect(credentials.every((credential) => credential === "omit")).toBe(true);
    expect(window.location.search).toBe("");

    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByRole("heading", { name: "View your API key usage" })).toBeInTheDocument();
    expect(screen.getByLabelText("API key")).toHaveValue("");
    expect(window.localStorage.getItem(KEY_DASHBOARD_API_KEY_STORAGE_KEY)).toBeNull();
  });

  it("remembers a valid key only after opt-in and restores it on the next mount", async () => {
    let profileCalls = 0;
    server.use(
      http.get("/api/key-dashboard/profile", ({ request }) => {
        profileCalls += 1;
        expect(request.headers.get("Authorization")).toBe(`Bearer ${TEST_KEY}`);
        return HttpResponse.json(safeProfile);
      }),
      http.get("/v1/usage", () => HttpResponse.json(usagePayload)),
      http.get("/api/key-dashboard/request-logs", () =>
        HttpResponse.json({ requests: [safeLog], total: 1, hasMore: false }),
      ),
    );
    const user = userEvent.setup();
    const firstRender = renderWithProviders(<App />);

    await user.click(await screen.findByRole("checkbox", { name: /Remember on this browser/ }));
    await user.type(screen.getByLabelText("API key"), TEST_KEY);
    await user.click(screen.getByRole("button", { name: "Open dashboard" }));

    expect(await screen.findByRole("heading", { name: "Production client" })).toBeInTheDocument();
    expect(window.localStorage.getItem(KEY_DASHBOARD_API_KEY_STORAGE_KEY)).toBe(TEST_KEY);

    firstRender.unmount();
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Production client" })).toBeInTheDocument();
    await waitFor(() => expect(profileCalls).toBe(2));
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(window.localStorage.getItem(KEY_DASHBOARD_API_KEY_STORAGE_KEY)).toBeNull();
  });

  it("returns to key entry with an independent invalid-key error", async () => {
    window.localStorage.setItem(KEY_DASHBOARD_API_KEY_STORAGE_KEY, TEST_KEY);
    server.use(
      http.get("/api/key-dashboard/profile", () =>
        HttpResponse.json(
          { error: { code: "invalid_api_key", message: "Invalid API key" } },
          { status: 401 },
        ),
      ),
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
    renderWithProviders(<App />);

    expect(await screen.findByText("This API key is invalid, inactive, or expired.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("API key")).toHaveValue(""));
    expect(window.localStorage.getItem(KEY_DASHBOARD_API_KEY_STORAGE_KEY)).toBeNull();
  });
});
