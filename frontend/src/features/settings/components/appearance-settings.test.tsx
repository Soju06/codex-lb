import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppearanceSettings } from "@/features/settings/components/appearance-settings";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { useThemeStore } from "@/hooks/use-theme";
import { useTimeFormatStore } from "@/hooks/use-time-format";
import type { DashboardSettings } from "@/features/settings/schemas";

function installLocalStorageMock() {
  const storage = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => {
        storage.set(key, value);
      },
      removeItem: (key: string) => {
        storage.delete(key);
      },
      clear: () => {
        storage.clear();
      },
    },
  });
}

const BASE_SETTINGS: DashboardSettings = {
  stickyThreadsEnabled: true,
  upstreamStreamTransport: "default",
  preferEarlierResetAccounts: true,
  prioritiesEnabled: false,
  routingStrategy: "capacity_weighted",
  openaiCacheAffinityMaxAgeSeconds: 1800,
  dashboardSessionTtlSeconds: 43200,
  importWithoutOverwrite: true,
  totpRequiredOnLogin: false,
  totpConfigured: false,
  apiKeyAuthEnabled: false,
};

describe("AppearanceSettings", () => {
  beforeEach(() => {
    installLocalStorageMock();
    useThemeStore.setState({ preference: "light", theme: "light", initialized: true });
    useTimeFormatStore.setState({ timeFormat: "12h" });
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "both" });
  });

  it("exposes selected state for the time-format toggle", async () => {
    const user = userEvent.setup();

    render(<AppearanceSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    const button12h = screen.getByRole("button", { name: /12h/i });
    const button24h = screen.getByRole("button", { name: /24h/i });

    expect(button12h).toHaveAttribute("aria-pressed", "true");
    expect(button24h).toHaveAttribute("aria-pressed", "false");

    await user.click(button24h);

    expect(button12h).toHaveAttribute("aria-pressed", "false");
    expect(button24h).toHaveAttribute("aria-pressed", "true");
    expect(useTimeFormatStore.getState().timeFormat).toBe("24h");
  });

  it("exposes selected state for the account quota toggle", async () => {
    const user = userEvent.setup();

    render(<AppearanceSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    const button5h = screen.getByRole("button", { name: "5H" });
    const buttonWeekly = screen.getByRole("button", { name: "W" });
    const buttonBoth = screen.getByRole("button", { name: "Both" });

    expect(buttonBoth).toHaveAttribute("aria-pressed", "true");
    expect(button5h).toHaveAttribute("aria-pressed", "false");
    expect(buttonWeekly).toHaveAttribute("aria-pressed", "false");

    await user.click(button5h);

    expect(button5h).toHaveAttribute("aria-pressed", "true");
    expect(useAccountQuotaDisplayStore.getState().quotaDisplay).toBe("5h");

    await user.click(buttonWeekly);

    expect(buttonWeekly).toHaveAttribute("aria-pressed", "true");
    expect(useAccountQuotaDisplayStore.getState().quotaDisplay).toBe("weekly");
  });

  it("saves the priorities toggle", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(<AppearanceSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    const prioritiesSwitch = screen.getByRole("switch");
    expect(prioritiesSwitch).toHaveAttribute("aria-checked", "false");

    await user.click(prioritiesSwitch);

    expect(onSave).toHaveBeenCalledWith({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      prioritiesEnabled: true,
      routingStrategy: "capacity_weighted",
      openaiCacheAffinityMaxAgeSeconds: 1800,
      dashboardSessionTtlSeconds: 43200,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: false,
    });
  });
});
