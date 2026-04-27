import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RoutingSettings } from "@/features/settings/components/routing-settings";
import type { DashboardSettings } from "@/features/settings/schemas";

const BASE_SETTINGS: DashboardSettings = {
  stickyThreadsEnabled: false,
  upstreamStreamTransport: "default",
  preferEarlierResetAccounts: true,
  routingStrategy: "usage_weighted",
  relativeAvailabilityPower: 2,
  relativeAvailabilityTopK: 5,
  openaiCacheAffinityMaxAgeSeconds: 300,
  importWithoutOverwrite: false,
  totpRequiredOnLogin: false,
  totpConfigured: false,
  apiKeyAuthEnabled: true,
};

describe("RoutingSettings", () => {
  it("saves a new prompt-cache affinity ttl from the button and Enter key", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />,
    );

    const ttlInput = screen.getByRole("spinbutton", { name: "Prompt-cache affinity TTL" });
    await user.clear(ttlInput);
    await user.type(ttlInput, "180");
    await user.click(screen.getByRole("button", { name: "Save TTL" }));

    expect(onSave).toHaveBeenCalledWith({
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      routingStrategy: "usage_weighted",
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 5,
      openaiCacheAffinityMaxAgeSeconds: 180,
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
    });

    rerender(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, openaiCacheAffinityMaxAgeSeconds: 180 }}
        busy={false}
        onSave={onSave}
      />,
    );

    await user.clear(screen.getByRole("spinbutton", { name: "Prompt-cache affinity TTL" }));
    await user.type(screen.getByRole("spinbutton", { name: "Prompt-cache affinity TTL" }), "240{Enter}");

    expect(onSave).toHaveBeenLastCalledWith({
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      routingStrategy: "usage_weighted",
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 5,
      openaiCacheAffinityMaxAgeSeconds: 240,
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
    });
  });

  it("disables ttl save for invalid values and saves sticky-thread toggles", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    const ttlInput = screen.getByRole("spinbutton", { name: "Prompt-cache affinity TTL" });
    const saveButton = screen.getByRole("button", { name: "Save TTL" });
    expect(saveButton).toBeDisabled();

    await user.clear(ttlInput);
    await user.type(ttlInput, "0");
    expect(saveButton).toBeDisabled();

    await user.click(screen.getAllByRole("switch")[0]!);

    expect(onSave).toHaveBeenCalledWith({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      routingStrategy: "usage_weighted",
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 5,
      openaiCacheAffinityMaxAgeSeconds: 300,
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
    });
  });

  it("shows relative availability controls only for that strategy", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <RoutingSettings settings={{ ...BASE_SETTINGS, routingStrategy: "relative_availability" }} busy={false} onSave={onSave} />,
    );

    expect(screen.getByRole("spinbutton", { name: "Relative availability power" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Relative availability top K" })).toBeInTheDocument();

    await user.clear(screen.getByRole("spinbutton", { name: "Relative availability power" }));
    await user.type(screen.getByRole("spinbutton", { name: "Relative availability power" }), "1.5");
    await user.click(screen.getByRole("button", { name: "Save power" }));

    expect(onSave).toHaveBeenCalledWith({
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      routingStrategy: "relative_availability",
      relativeAvailabilityPower: 1.5,
      relativeAvailabilityTopK: 5,
      openaiCacheAffinityMaxAgeSeconds: 300,
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
    });

    rerender(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);
    expect(screen.queryByRole("spinbutton", { name: "Relative availability power" })).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton", { name: "Relative availability top K" })).not.toBeInTheDocument();
  });

  it("shows the configured upstream transport", () => {
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    expect(screen.getByText("Upstream stream transport")).toBeInTheDocument();
    expect(screen.getByText("Server default")).toBeInTheDocument();
  });
});
