import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AddClaudeAccountOAuthDialog } from "@/features/claude/components/add-claude-account-oauth-dialog";
import * as claudeApi from "@/features/claude/api";
import type { ClaudeOauthStartResponse } from "@/features/claude/schemas";

const { toastError, toastSuccess } = vi.hoisted(() => ({
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    error: toastError,
    success: toastSuccess,
  },
}));

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
const originalIsSecureContext = Object.getOwnPropertyDescriptor(window, "isSecureContext");
const originalExecCommand = Object.getOwnPropertyDescriptor(document, "execCommand");

const START_RESPONSE: ClaudeOauthStartResponse = {
  flowId: "flow-1",
  authorizationUrl:
    "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e",
  stateToken: "state-token-xyz",
  expiresInSeconds: 600,
  callbackInstructions: "Open the URL, authorize, then copy the code from claude.ai and paste it here.",
  redirectUri: "https://platform.claude.com/oauth/code/callback",
};

describe("AddClaudeAccountOAuthDialog — Copy URL button", () => {
  beforeEach(() => {
    toastError.mockReset();
    toastSuccess.mockReset();
    vi.spyOn(claudeApi, "startClaudeOauth").mockResolvedValue(START_RESPONSE);
    vi.spyOn(claudeApi, "submitClaudeOauthCallback").mockResolvedValue({
      status: "success",
      account: {
        id: "acc-1",
        claudeAccountUuid: "acc-uuid-1",
        userEmail: null,
        userOrganizationUuid: null,
        status: "active",
        isActive: true,
        claudeAccessTokenExpiresAt: null,
        lastUsedAt: null,
        rateLimitRequestsRemaining: null,
        rateLimitInputTokensRemaining: null,
        rateLimitOutputTokensRemaining: null,
        rateLimitStatus: null,
        createdAt: new Date().toISOString(),
      },
    });
  });

  afterEach(() => {
    if (originalClipboard) {
      Object.defineProperty(navigator, "clipboard", originalClipboard);
    }
    if (originalIsSecureContext) {
      Object.defineProperty(window, "isSecureContext", originalIsSecureContext);
    }
    if (originalExecCommand) {
      Object.defineProperty(document, "execCommand", originalExecCommand);
    }
    vi.restoreAllMocks();
  });

  it("surfaces a failure toast when the Clipboard API and the execCommand fallback both fail", async () => {
    // This is the regression guard for openspec/.../fix-claude-oauth-link-endpoints:
    // the previous inline `navigator.clipboard.writeText` call swallowed rejection
    // with `void`, so operators saw no feedback when copy failed. The shared
    // `copyToClipboard` utility (via `<CopyButton>`) calls `toast.error("Failed to copy")`
    // on hard failure.
    const user = userEvent.setup();
    const writeText = vi.fn().mockRejectedValue(new Error("clipboard blocked"));
    const execCommand = vi.fn(() => false);

    Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    Object.defineProperty(document, "execCommand", { configurable: true, value: execCommand });

    render(
      <AddClaudeAccountOAuthDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />,
    );

    // Trigger the OAuth start so the dialog transitions to the "started" step.
    await user.click(screen.getByTestId("add-claude-account-oauth-start"));
    await screen.findByTestId("add-claude-account-oauth-form");

    const copyButton = screen.getByRole("button", { name: "Copy URL" });
    await user.click(copyButton);

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith("Failed to copy");
    });
  });

  it("copies the authorization URL via the shared utility on success", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);

    Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });

    render(
      <AddClaudeAccountOAuthDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />,
    );

    await user.click(screen.getByTestId("add-claude-account-oauth-start"));
    await screen.findByTestId("add-claude-account-oauth-form");

    const copyButton = screen.getByRole("button", { name: "Copy URL" });
    await user.click(copyButton);

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(START_RESPONSE.authorizationUrl);
      expect(toastSuccess).toHaveBeenCalledWith("Copied to clipboard");
    });
  });
});