import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { removeGuestPassword, setGuestPassword } from "@/features/auth/api";
import { GuestAccessSettings } from "@/features/settings/components/guest-access-settings";
import { createDashboardSettings } from "@/test/mocks/factories";

vi.mock("@/features/auth/api", () => ({
  removeGuestPassword: vi.fn(),
  setGuestPassword: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
  },
}));

describe("GuestAccessSettings", () => {
  it("saves guest access toggle changes through the shared settings payload", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const settings = createDashboardSettings({ guestAccessEnabled: false });

    render(
      <GuestAccessSettings
        settings={settings}
        busy={false}
        onSave={onSave}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await user.click(screen.getByRole("switch"));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        stickyThreadsEnabled: settings.stickyThreadsEnabled,
        preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
        guestAccessEnabled: true,
      }),
    );
  });

  it("sets an optional guest password and refreshes settings", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    vi.mocked(setGuestPassword).mockResolvedValue({ status: "ok" });

    render(
      <GuestAccessSettings
        settings={createDashboardSettings({ guestPasswordConfigured: false })}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
        onRefresh={onRefresh}
      />,
    );

    await user.type(screen.getByPlaceholderText("Optional guest password"), "guest-password-123");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(setGuestPassword).toHaveBeenCalledWith({ password: "guest-password-123" });
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(screen.getByPlaceholderText("Optional guest password")).toHaveValue("");
  });

  it("removes a configured guest password and refreshes settings", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    vi.mocked(removeGuestPassword).mockResolvedValue({ status: "ok" });

    render(
      <GuestAccessSettings
        settings={createDashboardSettings({ guestPasswordConfigured: true })}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
        onRefresh={onRefresh}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Remove" }));

    expect(removeGuestPassword).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
  });
});
