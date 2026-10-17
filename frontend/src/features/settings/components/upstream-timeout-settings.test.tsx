import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { UpstreamTimeoutSettings } from "@/features/settings/components/upstream-timeout-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings } from "@/features/settings/schemas";
import { createDashboardSettings } from "@/test/mocks/factories";

const TIMEOUT_NAMES = [
  "upstream_connect_timeout_seconds",
  "proxy_request_budget_seconds",
  "compact_request_budget_seconds",
  "transcription_request_budget_seconds",
  "http_responses_stream_request_budget_seconds",
  "http_responses_session_bridge_request_budget_seconds",
  "stream_idle_timeout_seconds",
  "proxy_downstream_websocket_idle_timeout_seconds",
  "sse_keepalive_interval_seconds",
] as const;

function settingsWithProvenance(
  overrides: Partial<DashboardSettings> = {},
  sources: Partial<Record<(typeof TIMEOUT_NAMES)[number], { source: "dashboard" | "env" | "default"; envValue: number; default: number }>> = {},
): DashboardSettings {
  const base = createDashboardSettings(overrides);
  const provenance: NonNullable<DashboardSettings["provenance"]> = {};
  const defaults: Record<(typeof TIMEOUT_NAMES)[number], number> = {
    upstream_connect_timeout_seconds: 8,
    proxy_request_budget_seconds: 600,
    compact_request_budget_seconds: 180,
    transcription_request_budget_seconds: 120,
    http_responses_stream_request_budget_seconds: 7200,
    http_responses_session_bridge_request_budget_seconds: 7200,
    stream_idle_timeout_seconds: 7200,
    proxy_downstream_websocket_idle_timeout_seconds: 120,
    sse_keepalive_interval_seconds: 10,
  };
  for (const name of TIMEOUT_NAMES) {
    provenance[name] = sources[name] ?? { source: "default", envValue: defaults[name], default: defaults[name] };
  }
  return { ...base, provenance };
}

describe("UpstreamTimeoutSettings", () => {
  it("renders inherited fields empty with their provenance badge and the effective value as placeholder", () => {
    const settings = settingsWithProvenance(
      { proxyRequestBudgetSeconds: 700 },
      { proxy_request_budget_seconds: { source: "env", envValue: 700, default: 600 } },
    );
    render(<UpstreamTimeoutSettings settings={settings} busy={false} onSave={vi.fn()} />);

    const budget = screen.getByRole("spinbutton", { name: "Proxy request budget" });
    expect(budget).toHaveValue(null);
    expect(budget).toHaveAttribute("placeholder", "700");
    expect(screen.getByText("Inherited from environment (700)")).toBeInTheDocument();
    expect(screen.getAllByText(/^Default \(/)).toHaveLength(8);
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeDisabled();
  });

  it("pre-fills dashboard-owned values and saves only the edited fields", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const settings = settingsWithProvenance(
      { sseKeepaliveIntervalSeconds: 5 },
      { sse_keepalive_interval_seconds: { source: "dashboard", envValue: 10, default: 10 } },
    );
    render(<UpstreamTimeoutSettings settings={settings} busy={false} onSave={onSave} />);

    expect(screen.getByRole("spinbutton", { name: "SSE keepalive interval" })).toHaveValue(5);
    expect(screen.getByRole("button", { name: "Reset to inherited" })).toBeInTheDocument();

    const streamIdle = screen.getByRole("spinbutton", { name: "Stream idle timeout" });
    await user.type(streamIdle, "900");
    await user.click(screen.getByRole("button", { name: "Save timeouts" }));

    expect(onSave).toHaveBeenCalledWith(
      buildSettingsUpdateRequest(settings, { streamIdleTimeoutSeconds: 900 }),
    );
  });

  it("clears a dashboard-owned value with an explicit null when its input is emptied", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const settings = settingsWithProvenance(
      { compactRequestBudgetSeconds: 240 },
      { compact_request_budget_seconds: { source: "dashboard", envValue: 180, default: 180 } },
    );
    render(<UpstreamTimeoutSettings settings={settings} busy={false} onSave={onSave} />);

    await user.clear(screen.getByRole("spinbutton", { name: "Compact request budget" }));
    await user.click(screen.getByRole("button", { name: "Save timeouts" }));

    expect(onSave).toHaveBeenCalledWith(
      buildSettingsUpdateRequest(settings, { compactRequestBudgetSeconds: null }),
    );
  });

  it("mirrors the backend invariants: positive values, keepalive 0 allowed, connect within the budgets", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<UpstreamTimeoutSettings settings={settingsWithProvenance()} busy={false} onSave={onSave} />);

    await user.type(screen.getByRole("spinbutton", { name: "Stream idle timeout" }), "0");
    expect(
      screen.getByText("Stream idle timeout must be a number greater than 0 (up to 86400 seconds)."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeDisabled();
    await user.clear(screen.getByRole("spinbutton", { name: "Stream idle timeout" }));

    await user.type(screen.getByRole("spinbutton", { name: "SSE keepalive interval" }), "0");
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeEnabled();

    // 130 s connect exceeds the inherited transcription budget (120 s) but not
    // the proxy budget (600 s) or the compact budget (180 s).
    await user.type(screen.getByRole("spinbutton", { name: "Upstream connect timeout" }), "130");
    expect(
      screen.getByText(
        "The connect timeout (130 s) must not exceed the Transcription request budget; the proxy clamps it to the budget anyway.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeDisabled();

    // Raising that budget in the same edit satisfies the rule on the effective values.
    await user.type(screen.getByRole("spinbutton", { name: "Transcription request budget" }), "150");
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Save timeouts" }));

    expect(onSave).toHaveBeenCalledWith(
      buildSettingsUpdateRequest(settingsWithProvenance(), {
        upstreamConnectTimeoutSeconds: 130,
        transcriptionRequestBudgetSeconds: 150,
        sseKeepaliveIntervalSeconds: 0,
      }),
    );
  });

  it("mirrors the stream and bridge budget invariants (M1)", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<UpstreamTimeoutSettings settings={settingsWithProvenance()} busy={false} onSave={onSave} />);

    // 100 s connect fits the proxy (600), compact (180) and transcription (120)
    // budgets but not a 60 s stream budget typed in the same edit.
    await user.type(screen.getByRole("spinbutton", { name: "Upstream connect timeout" }), "100");
    await user.type(screen.getByRole("spinbutton", { name: "Responses stream request budget" }), "60");
    expect(
      screen.getByText(
        "The connect timeout (100 s) must not exceed the Responses stream request budget; the proxy clamps it to the budget anyway.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeDisabled();
    await user.clear(screen.getByRole("spinbutton", { name: "Upstream connect timeout" }));
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeEnabled();

    // The bridge budget must exceed twice the fixed 300 s stuck gate.
    await user.type(screen.getByRole("spinbutton", { name: "Session bridge request budget" }), "600");
    expect(
      screen.getByText(
        "The session bridge request budget (600 s) must exceed 600 s (twice the fixed 300 s stuck-gate threshold).",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeDisabled();
    await user.type(screen.getByRole("spinbutton", { name: "Session bridge request budget" }), "1");
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Save timeouts" }));

    expect(onSave).toHaveBeenCalledWith(
      buildSettingsUpdateRequest(settingsWithProvenance(), {
        httpResponsesStreamRequestBudgetSeconds: 60,
        httpResponsesSessionBridgeRequestBudgetSeconds: 6001,
      }),
    );
  });

  it("mirrors connect-within-bridge-budget and the stream budget admission-wait floor (M1)", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    // Environment already raises the sibling budgets, so only the bridge budget
    // can be the one a 700 s connect timeout outgrows.
    const settings = settingsWithProvenance(
      {
        proxyRequestBudgetSeconds: 5000,
        compactRequestBudgetSeconds: 5000,
        transcriptionRequestBudgetSeconds: 5000,
      },
      {
        proxy_request_budget_seconds: { source: "env", envValue: 5000, default: 600 },
        compact_request_budget_seconds: { source: "env", envValue: 5000, default: 180 },
        transcription_request_budget_seconds: { source: "env", envValue: 5000, default: 120 },
      },
    );
    render(<UpstreamTimeoutSettings settings={settings} busy={false} onSave={onSave} />);

    await user.type(screen.getByRole("spinbutton", { name: "Upstream connect timeout" }), "700");
    await user.type(screen.getByRole("spinbutton", { name: "Session bridge request budget" }), "650");
    expect(
      screen.getByText(
        "The connect timeout (700 s) must not exceed the Session bridge request budget; the proxy clamps it to the budget anyway.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeDisabled();
    await user.clear(screen.getByRole("spinbutton", { name: "Upstream connect timeout" }));
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeEnabled();

    // The stream budget must still cover the fixed 10 s admission wait.
    await user.type(screen.getByRole("spinbutton", { name: "Responses stream request budget" }), "5");
    expect(
      screen.getByText(
        "The Responses stream request budget (5 s) must be at least 10 s (the fixed admission-wait timeout).",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeDisabled();
  });

  it("lets an unrelated edit through when the inherited values already violate an invariant", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    // Environment: connect 200 s > transcription budget 120 s (pre-existing violation).
    const settings = settingsWithProvenance(
      { upstreamConnectTimeoutSeconds: 200 },
      { upstream_connect_timeout_seconds: { source: "env", envValue: 200, default: 8 } },
    );
    render(<UpstreamTimeoutSettings settings={settings} busy={false} onSave={onSave} />);

    expect(screen.queryByText(/must not exceed/)).not.toBeInTheDocument();
    await user.type(screen.getByRole("spinbutton", { name: "Stream idle timeout" }), "900");
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeEnabled();

    // Introducing a new violation (proxy budget below the connect timeout) is still blocked.
    await user.type(screen.getByRole("spinbutton", { name: "Proxy request budget" }), "150");
    expect(screen.getByText(/must not exceed the Proxy request budget/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save timeouts" })).toBeDisabled();
  });

  it("falls back to placeholders without badges against a backend that reports no provenance", () => {
    const settings = { ...createDashboardSettings(), provenance: undefined };
    render(<UpstreamTimeoutSettings settings={settings} busy={false} onSave={vi.fn()} />);

    expect(screen.getByRole("spinbutton", { name: "Upstream connect timeout" })).toHaveAttribute("placeholder", "8");
    expect(screen.queryByText(/Inherited from environment/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Default \(/)).not.toBeInTheDocument();
  });
});
