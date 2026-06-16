import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { removeGuestPassword, setGuestPassword } from "@/features/auth/api";
import { GuestPasswordSetRequestSchema } from "@/features/auth/schemas";
import { GuestAccessSettings } from "@/features/settings/components/guest-access-settings";
import i18n from "@/i18n";
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

  it("localizes guest password validation keys instead of showing the raw key", async () => {
    const user = userEvent.setup();
    vi.mocked(setGuestPassword).mockRejectedValue(new Error("settings.password.validation.minLength"));

    await i18n.changeLanguage("zh-CN");

    try {
      render(
        <GuestAccessSettings
          settings={createDashboardSettings({ guestPasswordConfigured: false })}
          busy={false}
          onSave={vi.fn().mockResolvedValue(undefined)}
          onRefresh={vi.fn().mockResolvedValue(undefined)}
        />,
      );

      await user.type(screen.getByPlaceholderText("可选访客密码"), "short");
      await user.click(screen.getByRole("button", { name: "保存" }));

      expect(await screen.findByText("密码至少需要 8 个字符。")).toBeInTheDocument();
      expect(screen.queryByText("settings.password.validation.minLength")).not.toBeInTheDocument();
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("localizes imperatively parsed guest password validation errors", async () => {
    const user = userEvent.setup();
    vi.mocked(setGuestPassword).mockImplementation(() => {
      GuestPasswordSetRequestSchema.parse({ password: "short" });
      return Promise.resolve({ status: "ok" });
    });

    await i18n.changeLanguage("zh-CN");

    try {
      render(
        <GuestAccessSettings
          settings={createDashboardSettings({ guestPasswordConfigured: false })}
          busy={false}
          onSave={vi.fn().mockResolvedValue(undefined)}
          onRefresh={vi.fn().mockResolvedValue(undefined)}
        />,
      );

      await user.type(screen.getByPlaceholderText("可选访客密码"), "short");
      await user.click(screen.getByRole("button", { name: "保存" }));

      expect(await screen.findByText("密码至少需要 8 个字符。")).toBeInTheDocument();
      expect(screen.queryByText("settings.password.validation.minLength")).not.toBeInTheDocument();
    } finally {
      await i18n.changeLanguage("en");
    }
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
