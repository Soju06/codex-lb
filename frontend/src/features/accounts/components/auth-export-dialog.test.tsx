import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";

import { AuthExportDialog } from "./auth-export-dialog";

const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: toastSuccess,
    error: toastError,
  },
}));

const codexAuthStringContent = `${JSON.stringify(
    {
      auth_mode: "chatgpt",
      OPENAI_API_KEY: null,
      tokens: {
        id_token: "id-token-abcdefghijklmnopqrstuvwxyz-0123456789",
        access_token: "access-token-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        refresh_token: "refresh-token-abcdefghijklmnopqrstuvwxyz-0123456789",
        account_id: "chatgpt-acc-1",
      },
      last_refresh: "2026-01-01T00:00:00.000000Z",
    },
    null,
    2,
  )}\n`;

const exportData = {
  filename: "opencode-auth-user.json",
  account: {
    accountId: "acc-1",
    chatgptAccountId: "chatgpt-acc-1",
    email: "user@example.com",
  },
  tokens: {
    idToken: "id-token-abcdefghijklmnopqrstuvwxyz-0123456789",
    accessToken: "access-token-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    refreshToken: "refresh-token-abcdefghijklmnopqrstuvwxyz-0123456789",
    expiresAtMs: 2_000_000_000_000,
  },
  codexAuthJson: {
    authMode: "chatgpt",
    openaiApiKey: null,
    tokens: {
      idToken: "id-token-abcdefghijklmnopqrstuvwxyz-0123456789",
      accessToken: "access-token-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
      refreshToken: "refresh-token-abcdefghijklmnopqrstuvwxyz-0123456789",
      accountId: "chatgpt-acc-1" as string | null | undefined,
    },
    lastRefresh: "2026-01-01T00:00:00.000000Z",
  },
  opencodeAuthJson: {
    openai: {
      type: "oauth" as const,
      refresh: "refresh-token-abcdefghijklmnopqrstuvwxyz-0123456789",
      access: "access-token-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
      expires: 2_000_000_000_000,
      accountId: "chatgpt-acc-1",
    },
  },
};

describe("AuthExportDialog", () => {
  beforeEach(() => {
    toastSuccess.mockReset();
    toastError.mockReset();
    if (typeof HTMLElement !== "undefined" && typeof HTMLElement.prototype.hasPointerCapture !== "function") {
      HTMLElement.prototype.hasPointerCapture = () => false;
    }
    if (typeof HTMLElement !== "undefined" && typeof HTMLElement.prototype.scrollIntoView !== "function") {
      HTMLElement.prototype.scrollIntoView = () => {};
    }
  });

  it("copies auth.json in default codex mode", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    renderWithProviders(
      <AuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "Copy auth.json" }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(codexAuthStringContent);
    });
    expect(toastSuccess).toHaveBeenCalledWith("Copied to clipboard");
  });

  it("downloads auth.json in codex mode", async () => {
    const user = userEvent.setup();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const createObjectURL = vi.fn(() => "blob:auth-json");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });

    renderWithProviders(
      <AuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "Download" }));

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:auth-json");
  });

  it("shows codex token preview rows in codex mode by default", async () => {
    renderWithProviders(
      <AuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />,
    );

    expect(screen.getByText("Token preview")).toBeInTheDocument();
    expect(screen.getByText("ID token")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Copy access token" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Copy refresh token" })).toHaveLength(1);
  });

  it("switches to opencode token preview rows", async () => {
    const user = userEvent.setup();

    renderWithProviders(
      <AuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />,
    );

    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: "opencode" }));

    expect(screen.queryByText("ID token")).not.toBeInTheDocument();
    expect(screen.getByText("Access token")).toBeInTheDocument();
    expect(screen.getByText("Refresh token")).toBeInTheDocument();
  });

  it("copies the full codex id token from the preview row", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    renderWithProviders(
      <AuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />,
    );

    const idTokenRow = screen.getByText("ID token").closest("div.flex");
    expect(idTokenRow).not.toBeNull();

    await user.click(within(idTokenRow as HTMLElement).getByRole("button", { name: "Copy ID token" }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(exportData.codexAuthJson.tokens.idToken);
    });
  });

  it("displays Auth Export title and format selector", async () => {
    renderWithProviders(
      <AuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />,
    );

    expect(screen.getByText("Auth Export")).toBeInTheDocument();
    expect(screen.getByText("Format")).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.getByText(/This payload contains raw access and refresh tokens/i)).toBeInTheDocument();
  });
});
