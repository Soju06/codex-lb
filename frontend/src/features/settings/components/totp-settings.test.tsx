import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  confirmTotpSetup,
  disableTotp,
  startTotpSetup,
} from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { TotpSettings } from "@/features/settings/components/totp-settings";

vi.mock("@/features/auth/api", () => ({
  startTotpSetup: vi.fn(),
  confirmTotpSetup: vi.fn(),
  disableTotp: vi.fn(),
}));

const baseSettings = {
  stickyThreadsEnabled: true,
  preferEarlierResetAccounts: false,
  totpRequiredOnLogin: false,
  totpConfigured: false,
  apiKeyAuthEnabled: true,
};

describe("TotpSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({
      refreshSession: vi.fn().mockResolvedValue(undefined),
    });
  });

  it("supports setup flow and login requirement toggle", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    vi.mocked(startTotpSetup).mockResolvedValue({
      secret: "SECRET123",
      otpauthUri: "otpauth://totp/app?secret=SECRET123",
      qrSvgDataUri: "data:image/svg+xml;base64,PHN2Zy8+",
    });
    vi.mocked(confirmTotpSetup).mockResolvedValue({ status: "ok" });

    render(
      <TotpSettings
        settings={baseSettings}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Enable" }));
    expect(onSave).toHaveBeenCalledWith({
      stickyThreadsEnabled: true,
      preferEarlierResetAccounts: false,
      totpRequiredOnLogin: true,
      apiKeyAuthEnabled: true,
    });

    await user.click(screen.getByRole("button", { name: "Start setup" }));
    expect(startTotpSetup).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Secret: SECRET123")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "TOTP QR code" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Verification code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm setup" }));
    expect(confirmTotpSetup).toHaveBeenCalledWith({ secret: "SECRET123", code: "123456" });
    expect(await screen.findByText("TOTP configured.")).toBeInTheDocument();
  });

  it("supports disable flow when already configured", async () => {
    const user = userEvent.setup();
    vi.mocked(disableTotp).mockResolvedValue({ status: "ok" });

    render(
      <TotpSettings
        settings={{
          ...baseSettings,
          totpConfigured: true,
          totpRequiredOnLogin: true,
        }}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await user.type(screen.getByLabelText("Disable with TOTP code"), "654321");
    await user.click(screen.getByRole("button", { name: "Disable TOTP" }));

    expect(disableTotp).toHaveBeenCalledWith({ code: "654321" });
    expect(await screen.findByText("TOTP disabled.")).toBeInTheDocument();
  });
});
