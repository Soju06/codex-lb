import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  importWithoutOverwrite: false,
  totpRequiredOnLogin: false,
  totpConfigured: false,
  apiKeyAuthEnabled: true,
};

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("TotpSettings", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.clearAllMocks();
    useAuthStore.setState({
      refreshSession: vi.fn().mockResolvedValue(undefined),
    });
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it("shows setup button when not configured", () => {
    renderWithClient(
      <TotpSettings settings={baseSettings} onSave={vi.fn().mockResolvedValue(undefined)} />,
    );
    expect(screen.getByRole("button", { name: "Enable TOTP" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Disable" })).not.toBeInTheDocument();
  });

  it("shows disable button when configured", () => {
    renderWithClient(
      <TotpSettings
        settings={{ ...baseSettings, totpConfigured: true }}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.getByRole("button", { name: "Disable" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enable TOTP" })).not.toBeInTheDocument();
  });

  it("supports setup flow via dialog", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    vi.mocked(startTotpSetup).mockResolvedValue({
      secret: "SECRET123",
      otpauthUri: "otpauth://totp/app?secret=SECRET123",
      qrSvgDataUri: "data:image/svg+xml;base64,PHN2Zy8+",
    });
    vi.mocked(confirmTotpSetup).mockResolvedValue({ status: "ok" });

    renderWithClient(
      <TotpSettings settings={baseSettings} onSave={onSave} />,
    );

    await user.click(screen.getByRole("button", { name: "Enable TOTP" }));

    // Dialog opens with QR and secret
    expect(await screen.findByText("Secret: SECRET123")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "TOTP QR code" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Verification code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm setup" }));
    expect(confirmTotpSetup).toHaveBeenCalledWith({ secret: "SECRET123", code: "123456" });
  });

  it("toggles require-on-login via switch", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    renderWithClient(
      <TotpSettings settings={baseSettings} onSave={onSave} />,
    );

    await user.click(screen.getByRole("switch"));
    expect(onSave).toHaveBeenCalledWith({
      stickyThreadsEnabled: true,
      preferEarlierResetAccounts: false,
      importWithoutOverwrite: false,
      totpRequiredOnLogin: true,
      apiKeyAuthEnabled: true,
    });
  });

  it("supports disable flow via dialog", async () => {
    const user = userEvent.setup();
    vi.mocked(disableTotp).mockResolvedValue({ status: "ok" });

    renderWithClient(
      <TotpSettings
        settings={{
          ...baseSettings,
          totpConfigured: true,
          totpRequiredOnLogin: true,
        }}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Disable" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.type(screen.getByLabelText("TOTP code"), "654321");
    await user.click(screen.getByRole("button", { name: "Disable TOTP" }));
    expect(disableTotp).toHaveBeenCalledWith({ code: "654321" });
  });
});
