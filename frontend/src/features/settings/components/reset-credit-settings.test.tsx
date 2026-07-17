import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ResetCreditSettings } from "@/features/settings/components/reset-credit-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";
import { createDashboardSettings } from "@/test/mocks/factories";

describe("ResetCreditSettings", () => {
  it("renders the reset-credit switches with current settings", () => {
    render(
      <ResetCreditSettings
        settings={createDashboardSettings({
          showResetCreditBadges: true,
          autoRedeemResetCreditsBeforeExpiry: false,
          showResetCreditExpiryBadge: true,
        })}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByText("Reset credits")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Show reset-credit badges" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Auto-redeem reset credits before expiry" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "Show reset action expiry" })).toBeChecked();
    expect(
      screen.getByText("Attempts to redeem the soonest reset credit about 5 minutes before it expires."),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("switch").map((toggle) => toggle.getAttribute("aria-label"))).toEqual([
      "Show reset-credit badges",
      "Show reset action expiry",
      "Auto-redeem reset credits before expiry",
    ]);
  });

  it.each([
    {
      name: "Show reset-credit badges",
      settings: createDashboardSettings({ showResetCreditBadges: true }),
      patch: { showResetCreditBadges: false },
    },
    {
      name: "Auto-redeem reset credits before expiry",
      settings: createDashboardSettings({ autoRedeemResetCreditsBeforeExpiry: false }),
      patch: { autoRedeemResetCreditsBeforeExpiry: true },
    },
    {
      name: "Show reset action expiry",
      settings: createDashboardSettings({ showResetCreditExpiryBadge: true }),
      patch: { showResetCreditExpiryBadge: false },
    },
  ] satisfies Array<{
    name: string;
    settings: DashboardSettings;
    patch: Partial<SettingsUpdateRequest>;
  }>)("saves $name changes through the settings payload", async ({ name, settings, patch }) => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(<ResetCreditSettings settings={settings} busy={false} onSave={onSave} />);

    await user.click(screen.getByRole("switch", { name }));

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith(buildSettingsUpdateRequest(settings, patch));
  });

  it("disables reset-credit switches while settings are busy", () => {
    render(
      <ResetCreditSettings
        settings={createDashboardSettings()}
        busy={true}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole("switch", { name: "Show reset-credit badges" })).toBeDisabled();
    expect(screen.getByRole("switch", { name: "Auto-redeem reset credits before expiry" })).toBeDisabled();
    expect(screen.getByRole("switch", { name: "Show reset action expiry" })).toBeDisabled();
  });
});
