import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { OauthDialog } from "@/features/accounts/components/oauth-dialog";

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
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByText("OAuth failed unexpectedly")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    // Dialog footer has both "Try again" and "Close" buttons (plus the dialog's X close button)
    const closeButtons = screen.getAllByRole("button", { name: "Close" });
    expect(closeButtons.length).toBeGreaterThanOrEqual(1);
  });
});
