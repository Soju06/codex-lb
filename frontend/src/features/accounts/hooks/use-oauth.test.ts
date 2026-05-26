import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useOauth } from "@/features/accounts/hooks/use-oauth";

const startOauthMock = vi.fn();
const completeOauthMock = vi.fn();
const resetOauthMock = vi.fn();
const submitManualOauthCallbackMock = vi.fn();
const getOauthStatusMock = vi.fn();

vi.mock("@/features/accounts/api", () => ({
  startOauth: (...args: unknown[]) => startOauthMock(...args),
  completeOauth: (...args: unknown[]) => completeOauthMock(...args),
  resetOauth: (...args: unknown[]) => resetOauthMock(...args),
  submitManualOauthCallback: (...args: unknown[]) => submitManualOauthCallbackMock(...args),
  getOauthStatus: (...args: unknown[]) => getOauthStatusMock(...args),
}));

describe("useOauth", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetOauthMock.mockResolvedValue({ status: "reset" });
    getOauthStatusMock.mockResolvedValue({ status: "pending", errorMessage: null });
  });

  afterEach(() => {
    vi.useRealTimers();
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

  it("polls browser OAuth status even without a device interval", async () => {
    vi.useFakeTimers();
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
    getOauthStatusMock.mockResolvedValue({ status: "tokens_ready", errorMessage: null });

    const { result } = renderHook(() => useOauth());

    await act(async () => {
      await result.current.start("browser", { expectProxy: true });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(getOauthStatusMock).toHaveBeenCalledTimes(1);
    expect(result.current.state.status).toBe("tokens_ready");
  });

  it("does not trigger device completion when proxy finalization is expected", async () => {
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

    const { result } = renderHook(() => useOauth());

    await act(async () => {
      await result.current.start("device", {
        expectProxy: true,
        proxy: {
          host: "proxy.example.com",
          port: 1080,
          clearPassword: false,
          remoteDns: true,
        },
      });
    });

    expect(startOauthMock).toHaveBeenCalledWith({
      forceMethod: "device",
      expectProxy: true,
      proxyHost: "proxy.example.com",
      proxyPort: 1080,
      proxyRemoteDns: true,
    });
    expect(completeOauthMock).not.toHaveBeenCalled();
  });

  it("waits for an in-flight reset before starting a new OAuth attempt", async () => {
    let resolveReset!: (value: { status: string }) => void;
    resetOauthMock.mockReturnValue(
      new Promise((resolve) => {
        resolveReset = resolve;
      }),
    );
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
      void result.current.reset();
    });
    const start = act(async () => {
      await result.current.start("browser");
    });

    await Promise.resolve();
    expect(startOauthMock).not.toHaveBeenCalled();

    resolveReset({ status: "reset" });
    await start;

    expect(startOauthMock).toHaveBeenCalledTimes(1);
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

  it("updates state to tokens_ready after a deferred manual callback", async () => {
    submitManualOauthCallbackMock.mockResolvedValue({
      status: "tokens_ready",
      errorMessage: null,
    });

    const { result } = renderHook(() => useOauth());

    await act(async () => {
      await result.current.manualCallback("http://localhost:1455/auth/callback?code=ok&state=state");
    });

    expect(result.current.state.status).toBe("tokens_ready");
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
