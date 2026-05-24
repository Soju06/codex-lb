import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useOauth } from "@/features/accounts/hooks/use-oauth";

const startOauthMock = vi.fn();
const completeOauthMock = vi.fn();
const submitManualOauthCallbackMock = vi.fn();

vi.mock("@/features/accounts/api", () => ({
  startOauth: (...args: unknown[]) => startOauthMock(...args),
  completeOauth: (...args: unknown[]) => completeOauthMock(...args),
  submitManualOauthCallback: (...args: unknown[]) => submitManualOauthCallbackMock(...args),
  getOauthStatus: vi.fn().mockResolvedValue({ status: "pending", errorMessage: null }),
}));

describe("useOauth", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("starts device polling immediately after device OAuth start", async () => {
    startOauthMock.mockResolvedValue({
      flowId: "flow-device",
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
      flowId: "flow-device",
      deviceAuthId: "device-auth-id",
      userCode: "ABCD-1234",
    });
  });

  it("does not trigger device completion for browser OAuth start", async () => {
    startOauthMock.mockResolvedValue({
      flowId: "flow-browser",
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

  it("updates state to success after a successful manual callback", async () => {
    startOauthMock.mockResolvedValue({
      flowId: "flow-browser",
      method: "browser",
      authorizationUrl: "https://auth.example.com/authorize",
      callbackUrl: "http://127.0.0.1:1455/auth/callback",
      verificationUrl: null,
      userCode: null,
      deviceAuthId: null,
      intervalSeconds: null,
      expiresInSeconds: null,
    });
    submitManualOauthCallbackMock.mockResolvedValue({
      status: "success",
      errorMessage: null,
    });

    const { result } = renderHook(() => useOauth());

    await act(async () => {
      await result.current.start("browser");
    });

    await act(async () => {
      await result.current.manualCallback("http://localhost:1455/auth/callback?code=ok&state=state");
    });

    expect(submitManualOauthCallbackMock).toHaveBeenCalledWith({
      callbackUrl: "http://localhost:1455/auth/callback?code=ok&state=state",
      flowId: "flow-browser",
    });
    expect(result.current.state.status).toBe("success");
    expect(result.current.state.errorMessage).toBeNull();
  });

  it("updates state with the backend error after a failed manual callback", async () => {
    startOauthMock.mockResolvedValue({
      flowId: "flow-browser",
      method: "browser",
      authorizationUrl: "https://auth.example.com/authorize",
      callbackUrl: "http://127.0.0.1:1455/auth/callback",
      verificationUrl: null,
      userCode: null,
      deviceAuthId: null,
      intervalSeconds: null,
      expiresInSeconds: null,
    });
    submitManualOauthCallbackMock.mockResolvedValue({
      status: "error",
      errorMessage: "Invalid OAuth callback: state mismatch or missing code.",
    });

    const { result } = renderHook(() => useOauth());

    await act(async () => {
      await result.current.start("browser");
    });

    await act(async () => {
      await result.current.manualCallback("http://localhost:1455/auth/callback?code=bad&state=wrong");
    });

    expect(result.current.state.status).toBe("error");
    expect(result.current.state.errorMessage).toBe("Invalid OAuth callback: state mismatch or missing code.");
  });
});
