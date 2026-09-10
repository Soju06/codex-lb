import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BackgroundJobsSettings } from "@/features/settings/components/background-jobs-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings } from "@/features/settings/schemas";
import { createDashboardSettings } from "@/test/mocks/factories";

const PROVENANCE = {
  auth_guardian_enabled: { source: "default" as const, envValue: true, default: true },
  automations_scheduler_enabled: { source: "dashboard" as const, envValue: true, default: true },
  rate_limit_reset_credits_refresh_enabled: { source: "env" as const, envValue: false, default: true },
};

function renderSettings(settings: DashboardSettings) {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<BackgroundJobsSettings settings={settings} busy={false} onSave={onSave} />);
  return onSave;
}

describe("BackgroundJobsSettings", () => {
  it("renders the three switches with their effective values and inheritance badges", () => {
    renderSettings(
      createDashboardSettings({
        authGuardianEnabled: true,
        automationsSchedulerEnabled: false,
        rateLimitResetCreditsRefreshEnabled: false,
        provenance: PROVENANCE,
      }),
    );

    expect(screen.getByText("Background jobs")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Auth Guardian" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Automations scheduler" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "Reset-credit polling" })).not.toBeChecked();
    expect(screen.getByText("Default (on)")).toBeInTheDocument();
    expect(screen.getByText("Inherited from environment (off)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset to inherited" })).toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("explains when the topology blocks the guardian regardless of the switch", () => {
    renderSettings(
      createDashboardSettings({ authGuardianEnabled: true, authGuardianBlockedByTopology: true, provenance: PROVENANCE }),
    );

    expect(screen.getByRole("switch", { name: "Auth Guardian" })).toBeChecked();
    expect(screen.getByRole("note")).toHaveTextContent(/Blocked by topology/);
    expect(screen.getByRole("note")).toHaveTextContent("CODEX_LB_LEADER_ELECTION_ENABLED=true");
  });

  it("stores a dashboard value when a switch is flipped", async () => {
    const user = userEvent.setup();
    const settings = createDashboardSettings({ automationsSchedulerEnabled: true, provenance: PROVENANCE });
    const onSave = renderSettings(settings);

    await user.click(screen.getByRole("switch", { name: "Automations scheduler" }));

    expect(onSave).toHaveBeenCalledWith(
      buildSettingsUpdateRequest(settings, { automationsSchedulerEnabled: false }),
    );
  });

  it("resets a dashboard-owned toggle to inherited with an explicit null", async () => {
    const user = userEvent.setup();
    const settings = createDashboardSettings({ automationsSchedulerEnabled: false, provenance: PROVENANCE });
    const onSave = renderSettings(settings);

    await user.click(screen.getByRole("button", { name: "Reset to inherited" }));

    const payload = buildSettingsUpdateRequest(settings, { automationsSchedulerEnabled: null });
    expect(payload.automationsSchedulerEnabled).toBeNull();
    expect(onSave).toHaveBeenCalledWith(payload);
  });

  it("does not send the toggles when an unrelated setting is saved", () => {
    const settings = createDashboardSettings({ provenance: PROVENANCE });
    const payload = buildSettingsUpdateRequest(settings, { warmupModel: "gpt-5.6-sol" });
    expect("authGuardianEnabled" in payload).toBe(false);
    expect("automationsSchedulerEnabled" in payload).toBe(false);
    expect("rateLimitResetCreditsRefreshEnabled" in payload).toBe(false);
  });
});
