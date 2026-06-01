import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OauthDialog } from "@/features/accounts/components/oauth-dialog";

const { toastError } = vi.hoisted(() => ({
  toastError: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    error: toastError,
  },
}));

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
const originalIsSecureContext = Object.getOwnPropertyDescriptor(window, "isSecureContext");
const originalExecCommand = Object.getOwnPropertyDescriptor(document, "execCommand");

const idleState = {
  status: "idle" as const,
  method: null,
  authorizationUrl: null,
  callbackUrl: null,
  verificationUrl: null,
  userCode: null,
  deviceAuthId: null,
  intervalSeconds: null,
  expiresInSeconds: null,
  errorMessage: null,
};

const devicePendingState = {
  status: "pending" as const,
  method: "device" as const,
  authorizationUrl: null,
  callbackUrl: null,
  verificationUrl: "https://auth.example.com/device",
  userCode: "AAAA-BBBB",
  deviceAuthId: "device-auth-id",
  intervalSeconds: 5,
  expiresInSeconds: 120,
  errorMessage: null,
};

const browserPendingState = {
  status: "pending" as const,
  method: "browser" as const,
  authorizationUrl: "https://auth.example.com/authorize",
  callbackUrl: "http://127.0.0.1:1455/auth/callback",
  verificationUrl: null,
  userCode: null,
  deviceAuthId: null,
  intervalSeconds: null,
  expiresInSeconds: null,
  errorMessage: null,
};

const browserStartingState = {
  ...browserPendingState,
  status: "starting" as const,
};

const successState = {
  ...idleState,
  status: "success" as const,
};

const errorState = {
  ...idleState,
  status: "error" as const,
  errorMessage: "OAuth failed unexpectedly",
};

describe("OauthDialog", () => {
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
    toastError.mockReset();
    vi.restoreAllMocks();
  });

  it("keeps the browser-stage copy button focused after keyboard activation", async () => {
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

    render(
      <OauthDialog
        open
        state={browserPendingState}
        onOpenChange={vi.fn()}
        onStart={vi.fn().mockResolvedValue(undefined)}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    const copyButton = screen.getByRole("button", { name: "Copy" });
    copyButton.focus();
    expect(copyButton).toHaveFocus();

    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(copyButton).toHaveFocus();
    });
  });

  it("blurs the browser-stage copy button after pointer activation", async () => {
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

    render(
      <OauthDialog
        open
        state={browserPendingState}
        onOpenChange={vi.fn()}
        onStart={vi.fn().mockResolvedValue(undefined)}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    const copyButton = screen.getByRole("button", { name: "Copy" });
    copyButton.focus();
    expect(copyButton).toHaveFocus();

    await user.click(copyButton);

    await waitFor(() => {
      expect(copyButton).not.toHaveFocus();
    });
  });

  it("restores keyboard focus after fallback copy inside dialog", async () => {
    const user = userEvent.setup();
    const execCommand = vi.fn(() => {
      expect(document.activeElement?.tagName).toBe("TEXTAREA");
      return true;
    });

    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: false,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    render(
      <OauthDialog
        open
        state={browserPendingState}
        onOpenChange={vi.fn()}
        onStart={vi.fn().mockResolvedValue(undefined)}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    const copyButton = screen.getByRole("button", { name: "Copy" });
    copyButton.focus();
    expect(copyButton).toHaveFocus();

    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(execCommand).toHaveBeenCalledWith("copy");
      expect(copyButton).toHaveFocus();
    });
  });

  it("shows an error toast when browser-stage copy fails", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockRejectedValue(new Error("clipboard blocked"));
    const execCommand = vi.fn(() => false);

    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: true,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    render(
      <OauthDialog
        open
        state={browserPendingState}
        onOpenChange={vi.fn()}
        onStart={vi.fn().mockResolvedValue(undefined)}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Copy" }));

    await waitFor(() => {
      expect(execCommand).toHaveBeenCalledWith("copy");
      expect(toastError).toHaveBeenCalledWith("Failed to copy");
    });
  });

  it("renders intro stage with method selection and starts flow", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn().mockResolvedValue(undefined);

    render(
      <OauthDialog
        open
        state={idleState}
        onOpenChange={vi.fn()}
        onStart={onStart}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByText("Browser (PKCE)")).toBeInTheDocument();
    expect(screen.getByText("Device code")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start sign-in" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Start sign-in" }));
    expect(onStart).toHaveBeenCalledWith("browser");
  });

  it("renders device stage with user code and verification URL", () => {
    render(
      <OauthDialog
        open
        state={devicePendingState}
        onOpenChange={vi.fn()}
        onStart={vi.fn().mockResolvedValue(undefined)}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByText("AAAA-BBBB")).toBeInTheDocument();
    expect(screen.getByText("https://auth.example.com/device")).toBeInTheDocument();
    expect(screen.getByText(/Waiting for authorization/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change method" })).toBeInTheDocument();
  });

  it("renders success stage", () => {
    render(
      <OauthDialog
        open
        state={successState}
        onOpenChange={vi.fn()}
        onStart={vi.fn().mockResolvedValue(undefined)}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByText("Account has been added successfully.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
  });

  it("renders error stage with message and retry option", () => {
    render(
      <OauthDialog
        open
        state={errorState}
        onOpenChange={vi.fn()}
        onStart={vi.fn().mockResolvedValue(undefined)}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByText("OAuth failed unexpectedly")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    // Dialog footer has both "Try again" and "Close" buttons (plus the dialog's X close button)
    const closeButtons = screen.getAllByRole("button", { name: "Close" });
    expect(closeButtons.length).toBeGreaterThanOrEqual(1);
  });

  it("submits the pasted callback URL through the manual callback handler", async () => {
    const user = userEvent.setup();
    const onManualCallback = vi.fn().mockResolvedValue(undefined);

    render(
      <OauthDialog
        open
        state={browserPendingState}
        onOpenChange={vi.fn()}
        onStart={vi.fn().mockResolvedValue(undefined)}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={onManualCallback}
        onReset={vi.fn()}
      />,
    );

    await user.type(
      screen.getByPlaceholderText("http://localhost:1455/auth/callback?code=...&state=..."),
      "http://localhost:1455/auth/callback?code=abc&state=expected",
    );
    await user.click(screen.getByRole("button", { name: "Submit" }));

    expect(onManualCallback).toHaveBeenCalledWith(
      "http://localhost:1455/auth/callback?code=abc&state=expected",
    );
  });

  it("refreshes the browser authorization link without leaving the dialog", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn().mockResolvedValue(undefined);

    render(
      <OauthDialog
        open
        state={browserPendingState}
        onOpenChange={vi.fn()}
        onStart={onStart}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Refresh link" }));

    expect(onStart).toHaveBeenCalledWith("browser");
  });

  it("renders a disabled loading refresh state while generating a fresh browser link", () => {
    render(
      <OauthDialog
        open
        state={browserStartingState}
        onOpenChange={vi.fn()}
        onStart={vi.fn().mockResolvedValue(undefined)}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Refreshing..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Change method" })).toBeDisabled();
    expect(screen.getByText("Generating a fresh sign-in link...")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open sign-in page" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled();
  });

  it("clears the pasted callback input when browser refresh disables the form", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <OauthDialog
        open
        state={browserPendingState}
        onOpenChange={vi.fn()}
        onStart={vi.fn().mockResolvedValue(undefined)}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    const callbackInput = screen.getByPlaceholderText(
      "http://localhost:1455/auth/callback?code=...&state=...",
    );
    await user.type(callbackInput, "http://localhost:1455/auth/callback?code=abc&state=expected");
    expect(callbackInput).toHaveValue(
      "http://localhost:1455/auth/callback?code=abc&state=expected",
    );

    rerender(
      <OauthDialog
        open
        state={browserStartingState}
        onOpenChange={vi.fn()}
        onStart={vi.fn().mockResolvedValue(undefined)}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onManualCallback={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    const disabledCallbackInput = screen.getByRole("textbox");
    expect(disabledCallbackInput).toHaveValue("");
    expect(disabledCallbackInput).toBeDisabled();
    expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled();
  });
});
