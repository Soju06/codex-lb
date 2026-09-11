import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ThreadIdentitySettings } from "@/features/settings/components/thread-identity-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings } from "@/features/settings/schemas";
import { createDashboardSettings } from "@/test/mocks/factories";

const NAME = "account_scoped_thread_identity_enabled";
const LABEL = "Account-scoped thread identity";

function renderSettings(settings: DashboardSettings) {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<ThreadIdentitySettings settings={settings} busy={false} onSave={onSave} />);
  return onSave;
}

describe("ThreadIdentitySettings", () => {
  it("renders the switch with its effective value and the inheritance badge", () => {
    renderSettings(
      createDashboardSettings({
        accountScopedThreadIdentityEnabled: true,
        provenance: { [NAME]: { source: "env", envValue: true, default: false } },
      }),
    );

    expect(screen.getByText("Thread identity")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: LABEL })).toBeChecked();
    expect(screen.getByText(/never rewritten/i)).toBeInTheDocument();
    expect(screen.getByText("Inherited from environment (on)")).toBeInTheDocument();
  });

  it("shows the default badge when neither the dashboard nor the environment set the switch", () => {
    renderSettings(
      createDashboardSettings({ provenance: { [NAME]: { source: "default", envValue: false, default: false } } }),
    );

    expect(screen.getByRole("switch", { name: LABEL })).not.toBeChecked();
    expect(screen.getByText("Default (off)")).toBeInTheDocument();
  });

  it("stores a dashboard value when the switch is flipped", async () => {
    const user = userEvent.setup();
    const settings = createDashboardSettings({
      provenance: { [NAME]: { source: "default", envValue: false, default: false } },
    });
    const onSave = renderSettings(settings);

    await user.click(screen.getByRole("switch", { name: LABEL }));

    expect(onSave).toHaveBeenCalledWith(
      buildSettingsUpdateRequest(settings, { accountScopedThreadIdentityEnabled: true }),
    );
  });

  it("resets a dashboard-owned switch to inherited with an explicit null", async () => {
    const user = userEvent.setup();
    const settings = createDashboardSettings({
      accountScopedThreadIdentityEnabled: true,
      provenance: { [NAME]: { source: "dashboard", envValue: false, default: false } },
    });
    const onSave = renderSettings(settings);

    await user.click(screen.getByRole("button", { name: "Reset to inherited" }));

    const payload = buildSettingsUpdateRequest(settings, { accountScopedThreadIdentityEnabled: null });
    expect(payload.accountScopedThreadIdentityEnabled).toBeNull();
    expect(onSave).toHaveBeenCalledWith(payload);
  });

  it("does not send the switch when an unrelated setting is saved", () => {
    const settings = createDashboardSettings();
    const payload = buildSettingsUpdateRequest(settings, { warmupModel: "gpt-5.6-sol" });
    expect("accountScopedThreadIdentityEnabled" in payload).toBe(false);
  });
});
