import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AutomationsPauseToggle } from "@/features/automations/components/automations-pause-toggle";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings } from "@/features/settings/schemas";
import { createDashboardSettings } from "@/test/mocks/factories";

const mutateAsync = vi.fn();
let settingsData: DashboardSettings | undefined;

vi.mock("@/features/settings/hooks/use-settings", () => ({
  useSettings: () => ({
    settingsQuery: { data: settingsData, isFetching: false },
    updateSettingsMutation: { mutateAsync, isPending: false },
  }),
}));

describe("AutomationsPauseToggle", () => {
  beforeEach(() => {
    mutateAsync.mockReset();
    mutateAsync.mockResolvedValue(undefined);
    useAuthStore.setState({ canWrite: true });
  });

  it("renders nothing until the settings are loaded", () => {
    settingsData = undefined;
    const { container } = render(<AutomationsPauseToggle />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the running state and pauses through the shared dashboard setting", async () => {
    const user = userEvent.setup();
    settingsData = createDashboardSettings({
      automationsSchedulerEnabled: true,
      provenance: { automations_scheduler_enabled: { source: "default", envValue: true, default: true } },
    });
    render(<AutomationsPauseToggle />);

    const toggle = screen.getByRole("switch", { name: "Pause all automations" });
    expect(toggle).not.toBeChecked();
    expect(screen.queryByText("Paused")).not.toBeInTheDocument();
    expect(screen.getByText("Default (on)")).toBeInTheDocument();

    await user.click(toggle);

    expect(mutateAsync).toHaveBeenCalledWith(
      buildSettingsUpdateRequest(settingsData, { automationsSchedulerEnabled: false }),
    );
  });

  it("shows the paused state and resets to inherited with an explicit null", async () => {
    const user = userEvent.setup();
    settingsData = createDashboardSettings({
      automationsSchedulerEnabled: false,
      provenance: { automations_scheduler_enabled: { source: "dashboard", envValue: true, default: true } },
    });
    render(<AutomationsPauseToggle />);

    expect(screen.getByRole("switch", { name: "Pause all automations" })).toBeChecked();
    expect(screen.getByText("Paused")).toBeInTheDocument();
    expect(screen.getByText(/Run now is refused/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Reset to inherited" }));

    const payload = buildSettingsUpdateRequest(settingsData, { automationsSchedulerEnabled: null });
    expect(payload.automationsSchedulerEnabled).toBeNull();
    expect(mutateAsync).toHaveBeenCalledWith(payload);
  });

  it("disables the switch and the reset action for a read-only viewer", () => {
    settingsData = createDashboardSettings({
      automationsSchedulerEnabled: false,
      provenance: { automations_scheduler_enabled: { source: "dashboard", envValue: true, default: true } },
    });
    useAuthStore.setState({ canWrite: false });
    render(<AutomationsPauseToggle />);

    // The state stays readable (the badge and the paused copy), but writing the
    // shared setting needs write access, so neither control can start a PUT.
    expect(screen.getByRole("switch", { name: "Pause all automations" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reset to inherited" })).toBeDisabled();
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });
});
