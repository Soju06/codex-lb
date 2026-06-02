import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePrivacyStore } from "@/hooks/use-privacy";
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

function truncateSecret(value: string, leading = 18, trailing = 10): string {
  if (value.length <= leading + trailing + 1) return value;
  return `${value.slice(0, leading)}…${value.slice(-trailing)}`;
}

const idToken = "id-token-abcdefghijklmnopqrstuvwxyz-0123456789";
const accessToken =
  "access-token-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const refreshToken = "refresh-token-abcdefghijklmnopqrstuvwxyz-0123456789";

const codexAuthPreviewContent = `${JSON.stringify(
    {
      auth_mode: "chatgpt",
      OPENAI_API_KEY: null,
      tokens: {
        id_token: truncateSecret(idToken),
        access_token: truncateSecret(accessToken),
        refresh_token: truncateSecret(refreshToken),
        account_id: "chatgpt-acc-1",
      },
      last_refresh: "2026-01-01T00:00:00.000000Z",
    },
    null,
    2,
  )}\n`;

const codexAuthStringContent = `${JSON.stringify(
    {
      auth_mode: "chatgpt",
      OPENAI_API_KEY: null,
      tokens: {
        id_token: idToken,
        access_token: accessToken,
        refresh_token: refreshToken,
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
    idToken,
    accessToken,
    refreshToken,
    expiresAtMs: 2_000_000_000_000,
  },
  codexAuthJson: {
    auth_mode: "chatgpt",
    OPENAI_API_KEY: null,
    tokens: {
      id_token: idToken,
      access_token: accessToken,
      refresh_token: refreshToken,
      account_id: "chatgpt-acc-1" as string | null | undefined,
    },
    last_refresh: "2026-01-01T00:00:00.000000Z",
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
    usePrivacyStore.setState({ blurred: false });
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
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: true,
    });
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
    const append = vi.spyOn(document.body, "append");
    const remove = vi.spyOn(HTMLAnchorElement.prototype, "remove");
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
    expect(append).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(remove).toHaveBeenCalledOnce();
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
    expect(
      screen.getByText((_, element) => element?.tagName === "PRE" && element.textContent === codexAuthPreviewContent),
    ).toBeInTheDocument();
    expect(screen.queryByText("authMode")).not.toBeInTheDocument();
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
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: true,
    });
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
      expect(writeText).toHaveBeenCalledWith(exportData.codexAuthJson.tokens.id_token);
    });
  });

  it("displays Auth Export title and format selector", async () => {
    renderWithProviders(
      <AuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />,
    );

    expect(screen.getByText("Auth Export")).toBeInTheDocument();
    expect(screen.getByText("Format")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /format/i })).toBeInTheDocument();
    expect(screen.getByText(/This payload contains raw access and refresh tokens/i)).toBeInTheDocument();
  });

  it("blurs exported account email when privacy mode is enabled", async () => {
    usePrivacyStore.setState({ blurred: true });

    renderWithProviders(
      <AuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />,
    );

    expect(screen.getByText("user@example.com")).toHaveClass("privacy-blur");
  });

  it("resets the format to codex when reopened", async () => {
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(
      <AuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />,
    );

    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: "opencode" }));
    expect(screen.getByText("Access token")).toBeInTheDocument();

    rerender(<AuthExportDialog open={false} exportData={exportData} onOpenChange={vi.fn()} />);
    rerender(<AuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />);

    expect(screen.getByText("ID token")).toBeInTheDocument();
    expect(
      screen.getByText((_, element) => element?.tagName === "PRE" && element.textContent === codexAuthPreviewContent),
    ).toBeInTheDocument();
  });
});
