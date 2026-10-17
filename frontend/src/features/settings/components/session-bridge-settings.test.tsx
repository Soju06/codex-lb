import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SessionBridgeSettings } from "@/features/settings/components/session-bridge-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings } from "@/features/settings/schemas";
import { createDashboardSettings } from "@/test/mocks/factories";

const NAME = "http_responses_session_bridge_codex_prewarm_enabled";

function renderSettings(settings: DashboardSettings) {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<SessionBridgeSettings settings={settings} busy={false} onSave={onSave} />);
  return onSave;
}

describe("SessionBridgeSettings", () => {
  it("renders the Codex prewarm switch with its effective value and the inheritance badge", () => {
    renderSettings(
      createDashboardSettings({
        httpResponsesSessionBridgeCodexPrewarmEnabled: true,
        provenance: { [NAME]: { source: "env", envValue: true, default: false } },
      }),
    );

    expect(screen.getByText("Session bridge")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Codex session prewarm" })).toBeChecked();
    expect(screen.getByText(/one extra upstream request/i)).toBeInTheDocument();
    expect(screen.getByText("Inherited from environment (on)")).toBeInTheDocument();
  });

  it("shows the default badge when neither the dashboard nor the environment set the switch", () => {
    renderSettings(
      createDashboardSettings({ provenance: { [NAME]: { source: "default", envValue: false, default: false } } }),
    );

    expect(screen.getByRole("switch", { name: "Codex session prewarm" })).not.toBeChecked();
    expect(screen.getByText("Default (off)")).toBeInTheDocument();
  });

  it("stores a dashboard value when the switch is flipped", async () => {
    const user = userEvent.setup();
    const settings = createDashboardSettings({
      provenance: { [NAME]: { source: "default", envValue: false, default: false } },
    });
    const onSave = renderSettings(settings);

    await user.click(screen.getByRole("switch", { name: "Codex session prewarm" }));

    expect(onSave).toHaveBeenCalledWith(
      buildSettingsUpdateRequest(settings, { httpResponsesSessionBridgeCodexPrewarmEnabled: true }),
    );
  });

  it("resets a dashboard-owned switch to inherited with an explicit null", async () => {
    const user = userEvent.setup();
    const settings = createDashboardSettings({
      httpResponsesSessionBridgeCodexPrewarmEnabled: true,
      provenance: { [NAME]: { source: "dashboard", envValue: false, default: false } },
    });
    const onSave = renderSettings(settings);

    await user.click(screen.getByRole("button", { name: "Reset to inherited" }));

    const payload = buildSettingsUpdateRequest(settings, { httpResponsesSessionBridgeCodexPrewarmEnabled: null });
    expect(payload.httpResponsesSessionBridgeCodexPrewarmEnabled).toBeNull();
    expect(onSave).toHaveBeenCalledWith(payload);
  });

  it("does not send the switch when an unrelated setting is saved", () => {
    const settings = createDashboardSettings();
    const payload = buildSettingsUpdateRequest(settings, { warmupModel: "gpt-5.6-sol" });
    expect("httpResponsesSessionBridgeCodexPrewarmEnabled" in payload).toBe(false);
  });
});
