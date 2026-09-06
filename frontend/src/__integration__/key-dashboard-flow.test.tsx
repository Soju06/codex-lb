import { HttpResponse, http } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { KEY_DASHBOARD_API_KEY_STORAGE_KEY } from "@/features/key-dashboard/storage";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";
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
    useDateDisplayFormatStore.setState({ dateDisplayFormat: "iso8601" });
  });

  afterEach(() => {
    useDateDisplayFormatStore.setState({ dateDisplayFormat: "default" });
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
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
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("08:00:00  01/08/2026", { exact: true, normalizer: (value) => value })).toBeInTheDocument();
    expect(screen.getByText("gpt-5.1, gpt-5.2")).toBeInTheDocument();
    expect(screen.queryByText("Effective policies")).not.toBeInTheDocument();
    expect(screen.queryByText("Traffic class")).not.toBeInTheDocument();
    expect(screen.queryByText("Transport policy")).not.toBeInTheDocument();
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

  function mockDashboard() {
    server.use(
      http.get("/api/key-dashboard/profile", () => HttpResponse.json(safeProfile)),
      http.get("/v1/usage", () => HttpResponse.json(usagePayload)),
      http.get("/api/key-dashboard/request-logs", () => HttpResponse.json({ requests: [], total: 0, hasMore: false })),
    );
  }

  async function connect(user: ReturnType<typeof userEvent.setup>, key = TEST_KEY) {
    await user.type(await screen.findByLabelText("API key"), key);
    await user.click(screen.getByRole("button", { name: "Open dashboard" }));
    await screen.findByRole("tab", { name: "Install" });
  }

  it.each([null, "gpt-5.6-sol"])("renders unrestricted or enforced models and missing lifecycle dates (%s)", async (model) => {
    mockDashboard();
    server.use(http.get("/api/key-dashboard/profile", () => HttpResponse.json({
      ...safeProfile, allowedModels: null, enforcedModel: model, expiresAt: null, lastUsedAt: null,
    })));
    const user = userEvent.setup();
    renderWithProviders(<App />);
    await connect(user);
    expect(screen.getByText(model ?? "All models")).toBeInTheDocument();
    expect(screen.getByText("Never")).toBeInTheDocument();
    expect(screen.getByText("Not used yet")).toBeInTheDocument();
    screen.getByRole("tab", { name: "Overview" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Install" })).toHaveFocus();
  });

  it("supports keyboard platform selection and shows setup guidance outside the preview", async () => {
    mockDashboard();
    server.use(http.get("/api/key-dashboard/install-script", ({ request }) => {
      const platform = new URL(request.url).searchParams.get("platform");
      return HttpResponse.text(`# ${platform}\n${TEST_KEY}\n`);
    }));
    const user = userEvent.setup();
    renderWithProviders(<App />);
    await connect(user);
    await user.click(screen.getByRole("tab", { name: "Install" }));
    await screen.findByRole("button", { name: "Copy command" });

    const group = screen.getByRole("group", { name: "Operating system" });
    const macos = within(group).getByRole("radio", { name: "macOS" });
    const linux = within(group).getByRole("radio", { name: "Linux" });
    const windows = within(group).getByRole("radio", { name: "Windows" });
    macos.focus();
    expect(macos).toBeChecked();
    await user.keyboard("{ArrowRight}");
    expect(linux).toHaveFocus();
    expect(linux).toBeChecked();
    expect(await screen.findByText("codex-lb-linux.sh", { exact: true })).toBeInTheDocument();
    await user.keyboard("{ArrowRight}");
    expect(windows).toHaveFocus();
    expect(windows).toBeChecked();
    expect(await screen.findByText("codex-lb-windows.ps1", { exact: true })).toBeInTheDocument();
    await user.tab();
    expect(screen.getByRole("button", { name: "Copy command" })).toHaveFocus();
    const guidance = screen.getByRole("complementary", { name: "Before you run" });
    expect(within(guidance).getByText(/Install the Codex client first/)).toBeVisible();
    expect(within(guidance).getByText(/Backs up and replaces/)).toBeVisible();
    expect(within(guidance).getByText(/commands may remain in shell history/)).toBeVisible();
    const preview = screen.getByText("Preview script", { selector: "summary" });
    expect(preview.closest("details")).not.toHaveAttribute("open");
    await user.click(preview);
    expect(preview.closest("details")).toHaveAttribute("open");
    expect(document.body.textContent).not.toContain(TEST_KEY);
  });

  it("exports the logged-in key for every platform while masking previews and omitting cookies", async () => {
    mockDashboard();
    const requests: string[] = [];
    server.use(http.get("/api/key-dashboard/install-script", ({ request }) => {
      expect(request.headers.get("Authorization")).toBe(`Bearer ${TEST_KEY}`);
      expect(request.credentials).toBe("omit");
      expect(request.cache).toBe("no-store");
      expect(request.url).not.toContain(TEST_KEY);
      const platform = new URL(request.url).searchParams.get("platform");
      requests.push(platform!);
      return HttpResponse.text(`# ${platform}\n${TEST_KEY}\n`);
    }));
    const user = userEvent.setup();
    const clipboard = vi.spyOn(navigator.clipboard, "writeText");
    vi.stubGlobal("isSecureContext", true);
    const createObjectURL = vi.fn<(blob: Blob) => string>(() => "blob:installer");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    });
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderWithProviders(<App />);
    await connect(user);
    expect(requests).toHaveLength(0);
    await user.click(screen.getByRole("tab", { name: "Install" }));
    expect(screen.queryByRole("heading", { name: "Recent requests" })).not.toBeInTheDocument();
    for (const [platform, label] of [["macos", "macOS"], ["linux", "Linux"], ["windows", "Windows"]]) {
      await user.click(screen.getByRole("radio", { name: label }));
      await user.click(await screen.findByRole("button", { name: "Copy script" }));
      expect(screen.getByText(`codex-lb-${platform}.${platform === "windows" ? "ps1" : "sh"}`, { exact: true })).toBeInTheDocument();
      expect(screen.getByText(platform === "windows" ? "PowerShell" : "Bash", { selector: "span:not([aria-hidden])", exact: true })).toBeInTheDocument();
      expect(clipboard).toHaveBeenLastCalledWith(`# ${platform}\n${TEST_KEY}\n`);
      expect(document.body.textContent).not.toContain(TEST_KEY);
      await user.click(screen.getByRole("button", { name: "Copy command" }));
      const command = clipboard.mock.calls.at(-1)![0];
      expect(command).toContain(`Authorization: Bearer ${TEST_KEY}`);
      expect(command).toContain(`platform=${platform}`);
      expect(command).toContain(platform === "windows" ? "curl.exe" : "curl -fsS");
      await user.click(screen.getByRole("button", { name: "Download script" }));
      expect(anchorClick.mock.instances.at(-1)).toHaveAttribute("download", `codex-lb-${platform}.${platform === "windows" ? "ps1" : "sh"}`);
      const blob = createObjectURL.mock.calls.at(-1)![0] as Blob;
      const content = await new Promise<string>((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.readAsText(blob);
      });
      expect(content).toBe(`# ${platform}\n${TEST_KEY}\n`);
    }
    expect(requests).toEqual(["macos", "linux", "windows"]);
    expect(window.localStorage.getItem(KEY_DASHBOARD_API_KEY_STORAGE_KEY)).toBeNull();
    await user.click(screen.getByRole("tab", { name: "Overview" }));
    expect(await screen.findByRole("heading", { name: "Production client" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy script" })).not.toBeInTheDocument();
  });

  it("handles installer failures and invalid credentials without administrator authentication", async () => {
    mockDashboard();
    let status = 500;
    const adminAuth = vi.fn();
    server.use(
      http.get("/api/dashboard-auth/session", adminAuth),
      http.get("/api/key-dashboard/install-script", () => HttpResponse.json({ error: { message: "failed" } }, { status })),
    );
    const user = userEvent.setup();
    renderWithProviders(<App />);
    await connect(user);
    await user.click(screen.getByRole("tab", { name: "Install" }));
    expect(await screen.findByText("Could not load the setup script. Please try again.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy script" })).not.toBeInTheDocument();
    status = 401;
    await user.click(screen.getAllByRole("button", { name: "Refresh" }).at(-1)!);
    expect(await screen.findByRole("button", { name: "Open dashboard" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Install" })).not.toBeInTheDocument();
    expect(adminAuth).not.toHaveBeenCalled();
  });

  it("discards stale platform responses and exports only the new key after reconnecting", async () => {
    mockDashboard();
    let release: () => void = () => {};
    const delayed = new Promise<void>((resolve) => { release = resolve; });
    server.use(http.get("/api/key-dashboard/install-script", async ({ request }) => {
      const platform = new URL(request.url).searchParams.get("platform");
      const key = request.headers.get("Authorization")!.slice(7);
      if (platform === "macos" && key === TEST_KEY) await delayed;
      return HttpResponse.text(`# ${platform}\n${key}\n`);
    }));
    const user = userEvent.setup();
    const clipboard = vi.spyOn(navigator.clipboard, "writeText");
    vi.stubGlobal("isSecureContext", true);
    renderWithProviders(<App />);
    await connect(user);
    await user.click(screen.getByRole("tab", { name: "Install" }));
    await user.click(screen.getByRole("radio", { name: "Linux" }));
    await user.click(await screen.findByRole("button", { name: "Copy script" }));
    expect(clipboard).toHaveBeenLastCalledWith(`# linux\n${TEST_KEY}\n`);
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    release();
    await connect(user, "sk-clb-new-key");
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "Install" }));
    await user.click(await screen.findByRole("button", { name: "Copy script" }));
    expect(clipboard).toHaveBeenLastCalledWith("# macos\nsk-clb-new-key\n");
    expect(document.body.textContent).not.toContain(TEST_KEY);
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
