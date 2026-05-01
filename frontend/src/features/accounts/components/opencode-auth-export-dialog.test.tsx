import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";

import { OpenCodeAuthExportDialog } from "./opencode-auth-export-dialog";

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

const exportData = {
  filename: "opencode-auth-user.json",
  account: {
    accountId: "acc-1",
    chatgptAccountId: "chatgpt-acc-1",
    email: "user@example.com",
  },
  authJson: {
    openai: {
      type: "oauth" as const,
      refresh: "refresh-token-abcdefghijklmnopqrstuvwxyz-0123456789",
      access: "access-token-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
      expires: 2_000_000_000_000,
      accountId: "chatgpt-acc-1",
    },
  },
};

describe("OpenCodeAuthExportDialog", () => {
  beforeEach(() => {
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it("copies only the official OpenCode auth.json payload", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    renderWithProviders(
      <OpenCodeAuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "Copy auth.json" }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(`${JSON.stringify(exportData.authJson, null, 2)}\n`);
    });
    expect(writeText.mock.calls[0]?.[0]).not.toContain("user@example.com");
    expect(toastSuccess).toHaveBeenCalledWith("Copied to clipboard");
  });

  it("downloads the official OpenCode auth.json payload", async () => {
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
      <OpenCodeAuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "Download" }));

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:auth-json");
  });

  it("shows truncated token previews but copies the full access token", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    renderWithProviders(
      <OpenCodeAuthExportDialog open exportData={exportData} onOpenChange={vi.fn()} />,
    );

    expect(screen.getByText(/Truncated on screen for readability/i)).toBeInTheDocument();
    expect(screen.getAllByText(/access-token-abcde/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/QRSTUVWXYZ/).length).toBeGreaterThan(0);
    expect(screen.queryByText(exportData.authJson.openai.access)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Copy access token" }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(exportData.authJson.openai.access);
    });
  });
});
