import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { RequestVisibilitySettings } from "@/features/settings/components/request-visibility-settings";
import type { DashboardSettings } from "@/features/settings/schemas";

const BASE_SETTINGS: DashboardSettings = {
  stickyThreadsEnabled: false,
  upstreamStreamTransport: "default",
  preferEarlierResetAccounts: true,
  routingStrategy: "usage_weighted",
  openaiCacheAffinityMaxAgeSeconds: 300,
  importWithoutOverwrite: false,
  totpRequiredOnLogin: false,
  totpConfigured: false,
  apiKeyAuthEnabled: true,
  requestVisibilityMode: "off",
  requestVisibilityExpiresAt: null,
  requestVisibilityEnabled: false,
};

beforeAll(() => {
  if (!HTMLElement.prototype.hasPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
      configurable: true,
      value: () => false,
    });
  }
  if (!HTMLElement.prototype.setPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
      configurable: true,
      value: () => undefined,
    });
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
      configurable: true,
      value: () => undefined,
    });
  }
  if (!HTMLElement.prototype.scrollIntoView) {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: () => undefined,
    });
  }
});

describe("RequestVisibilitySettings", () => {
  it("shows current temporary status", () => {
    render(
      <RequestVisibilitySettings
        settings={{
          ...BASE_SETTINGS,
          requestVisibilityMode: "temporary",
          requestVisibilityExpiresAt: "2026-04-03T03:15:00Z",
          requestVisibilityEnabled: true,
        }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getAllByText("Temporary")).toHaveLength(2);
    expect(screen.getByText(/Enabled until/i)).toBeInTheDocument();
  });

  it("saves persistent mode", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(<RequestVisibilitySettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    await user.click(screen.getByLabelText("Request visibility mode"));
    await user.click(screen.getByRole("option", { name: "On" }));
    await user.click(screen.getByRole("button", { name: "Save policy" }));

    expect(onSave).toHaveBeenCalledWith({
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      routingStrategy: "usage_weighted",
      openaiCacheAffinityMaxAgeSeconds: 300,
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
      requestVisibilityMode: "persistent",
    });
  });

  it("saves temporary mode with duration", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(<RequestVisibilitySettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    await user.click(screen.getByLabelText("Request visibility mode"));
    await user.click(screen.getByRole("option", { name: "Temporary" }));
    await user.click(screen.getByLabelText("Temporary visibility duration"));
    await user.click(screen.getByRole("option", { name: "4 hours" }));
    await user.click(screen.getByRole("button", { name: "Enable temporarily" }));

    expect(onSave).toHaveBeenCalledWith({
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      routingStrategy: "usage_weighted",
      openaiCacheAffinityMaxAgeSeconds: 300,
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
      requestVisibilityMode: "temporary",
      requestVisibilityDurationMinutes: 240,
    });
  });
});
