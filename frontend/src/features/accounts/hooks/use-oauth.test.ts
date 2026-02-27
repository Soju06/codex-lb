import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useOauth } from "@/features/accounts/hooks/use-oauth";

const startOauthMock = vi.fn();
const completeOauthMock = vi.fn();

vi.mock("@/features/accounts/api", () => ({
  startOauth: (...args: unknown[]) => startOauthMock(...args),
  completeOauth: (...args: unknown[]) => completeOauthMock(...args),
  getOauthStatus: vi.fn().mockResolvedValue({ status: "pending", errorMessage: null }),
}));

describe("useOauth", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("starts device polling immediately after device OAuth start", async () => {
    startOauthMock.mockResolvedValue({
      method: "device",
      authorizationUrl: null,
      callbackUrl: null,
      verificationUrl: "https://auth.example.com/device",
      userCode: "ABCD-1234",
      deviceAuthId: "device-auth-id",
      intervalSeconds: 5,
      expiresInSeconds: 600,
    });
    completeOauthMock.mockResolvedValue({ status: "pending" });

    const { result } = renderHook(() => useOauth());

    await act(async () => {
      await result.current.start("device");
    });

    expect(completeOauthMock).toHaveBeenCalledTimes(1);
    expect(completeOauthMock).toHaveBeenCalledWith({
      deviceAuthId: "device-auth-id",
      userCode: "ABCD-1234",
    });
  });

  it("does not trigger device completion for browser OAuth start", async () => {
    startOauthMock.mockResolvedValue({
      method: "browser",
      authorizationUrl: "https://auth.example.com/authorize",
      callbackUrl: "http://127.0.0.1:1455/auth/callback",
      verificationUrl: null,
      userCode: null,
      deviceAuthId: null,
      intervalSeconds: null,
      expiresInSeconds: null,
    });

    const { result } = renderHook(() => useOauth());

    await act(async () => {
      await result.current.start("browser");
    });

    expect(completeOauthMock).not.toHaveBeenCalled();
  });
});
