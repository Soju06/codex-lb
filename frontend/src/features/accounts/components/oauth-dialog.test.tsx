import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { OauthDialog } from "@/features/accounts/components/oauth-dialog";

const pendingState = {
  status: "pending" as const,
  method: "device" as const,
  authorizationUrl: "https://auth.example.com/start",
  callbackUrl: "http://localhost:3000/api/oauth/callback",
  verificationUrl: "https://auth.example.com/device",
  userCode: "AAAA-BBBB",
  deviceAuthId: "device-auth-id",
  intervalSeconds: 5,
  expiresInSeconds: 120,
  errorMessage: "pending notice",
};

describe("OauthDialog", () => {
  it("renders state details and triggers flow actions", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn().mockResolvedValue(undefined);
    const onComplete = vi.fn().mockResolvedValue(undefined);

    render(
      <OauthDialog
        open
        state={pendingState}
        onOpenChange={vi.fn()}
        onStart={onStart}
        onComplete={onComplete}
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByText("Method:")).toBeInTheDocument();
    expect(screen.getByText("pending notice")).toBeInTheDocument();
    expect(screen.getByText("AAAA-BBBB")).toBeInTheDocument();
    expect(screen.getByText("Open authorization URL")).toBeInTheDocument();
    expect(screen.getByText("Open device verification URL")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Start Browser Flow" }));
    await user.click(screen.getByRole("button", { name: "Start Device Flow" }));
    await user.click(screen.getByRole("button", { name: "Complete OAuth" }));

    expect(onStart).toHaveBeenNthCalledWith(1, "browser");
    expect(onStart).toHaveBeenNthCalledWith(2, "device");
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("renders without state details when idle", () => {
    render(
      <OauthDialog
        open
        state={{
          status: "idle",
          method: null,
          authorizationUrl: null,
          callbackUrl: null,
          verificationUrl: null,
          userCode: null,
          deviceAuthId: null,
          intervalSeconds: null,
          expiresInSeconds: null,
          errorMessage: null,
        }}
        onOpenChange={vi.fn()}
        onStart={vi.fn().mockResolvedValue(undefined)}
        onComplete={vi.fn().mockResolvedValue(undefined)}
        onReset={vi.fn()}
      />,
    );

    expect(screen.queryByText("Method:")).not.toBeInTheDocument();
    expect(screen.queryByText("Open authorization URL")).not.toBeInTheDocument();
  });
});
