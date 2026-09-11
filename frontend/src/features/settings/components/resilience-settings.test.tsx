import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ResilienceSettings } from "@/features/settings/components/resilience-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings } from "@/features/settings/schemas";
import { createDashboardSettings } from "@/test/mocks/factories";

const PROVENANCE = {
  soft_drain_enabled: { source: "default" as const, envValue: true, default: true },
  deterministic_failover_enabled: { source: "env" as const, envValue: false, default: true },
  circuit_breaker_enabled: { source: "dashboard" as const, envValue: false, default: false },
};

function renderSettings(settings: DashboardSettings) {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<ResilienceSettings settings={settings} busy={false} onSave={onSave} />);
  return onSave;
}

describe("ResilienceSettings", () => {
  it("renders the three switches with their effective values and inheritance badges", () => {
    renderSettings(
      createDashboardSettings({
        softDrainEnabled: true,
        deterministicFailoverEnabled: false,
        circuitBreakerEnabled: true,
        provenance: PROVENANCE,
      }),
    );

    expect(screen.getByText("Resilience")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Soft drain" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Deterministic failover" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "Circuit breaker" })).toBeChecked();
    // Booleans are labelled on/off in the inheritance badge.
    expect(screen.getByText("Default (on)")).toBeInTheDocument();
    expect(screen.getByText("Inherited from environment (off)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset to inherited" })).toBeInTheDocument();
  });

  it("stores a dashboard value when a switch is flipped", async () => {
    const user = userEvent.setup();
    const settings = createDashboardSettings({ softDrainEnabled: true, provenance: PROVENANCE });
    const onSave = renderSettings(settings);

    await user.click(screen.getByRole("switch", { name: "Soft drain" }));

    expect(onSave).toHaveBeenCalledWith(buildSettingsUpdateRequest(settings, { softDrainEnabled: false }));
  });

  it("resets a dashboard-owned toggle to inherited with an explicit null", async () => {
    const user = userEvent.setup();
    const settings = createDashboardSettings({ circuitBreakerEnabled: true, provenance: PROVENANCE });
    const onSave = renderSettings(settings);

    await user.click(screen.getByRole("button", { name: "Reset to inherited" }));

    const payload = buildSettingsUpdateRequest(settings, { circuitBreakerEnabled: null });
    expect(payload.circuitBreakerEnabled).toBeNull();
    expect(onSave).toHaveBeenCalledWith(payload);
  });

  it("does not send the toggles when an unrelated setting is saved", () => {
    const settings = createDashboardSettings({ provenance: PROVENANCE });
    const payload = buildSettingsUpdateRequest(settings, { warmupModel: "gpt-5.6-sol" });
    expect("softDrainEnabled" in payload).toBe(false);
    expect("deterministicFailoverEnabled" in payload).toBe(false);
    expect("circuitBreakerEnabled" in payload).toBe(false);
  });
});
