import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useOauth } from "@/features/accounts/hooks/use-oauth";

const startOauthMock = vi.fn();
const completeOauthMock = vi.fn();
const submitManualOauthCallbackMock = vi.fn();
const getOauthStatusMock = vi.fn();

vi.mock("@/features/accounts/api", () => ({
  startOauth: (...args: unknown[]) => startOauthMock(...args),
  completeOauth: (...args: unknown[]) => completeOauthMock(...args),
  submitManualOauthCallback: (...args: unknown[]) => submitManualOauthCallbackMock(...args),
  getOauthStatus: (...args: unknown[]) => getOauthStatusMock(...args),
}));

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function renderUseOauth(queryClient = createTestQueryClient()) {
  return {
    queryClient,
    ...renderHook(() => useOauth(), {
      wrapper: createWrapper(queryClient),
    }),
  };
}

describe("useOauth", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getOauthStatusMock.mockResolvedValue({ status: "pending", errorMessage: null });
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

    const { result } = renderUseOauth();

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

    const { result } = renderUseOauth();

    await act(async () => {
      await result.current.start("browser");
    });

    expect(completeOauthMock).not.toHaveBeenCalled();
  });

  it("invalidates account and dashboard queries after browser OAuth completion", async () => {
    completeOauthMock.mockResolvedValue({ status: "success" });
    const { queryClient, result } = renderUseOauth();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    await act(async () => {
      await result.current.complete();
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "list"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "trends"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard", "overview"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard", "projections"] });
  });

  it("does not invalidate account or dashboard queries when OAuth completion stays pending", async () => {
    completeOauthMock.mockResolvedValue({ status: "pending", errorMessage: null });
    const { queryClient, result } = renderUseOauth();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    await act(async () => {
      await result.current.complete();
    });

    expect(result.current.state.status).toBe("pending");
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it("does not invalidate account or dashboard queries when OAuth completion returns an error", async () => {
    completeOauthMock.mockResolvedValue({
      status: "error",
      errorMessage: "OAuth flow is not ready yet.",
    });
    const { queryClient, result } = renderUseOauth();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    await act(async () => {
      await result.current.complete();
    });

    expect(result.current.state.status).toBe("error");
    expect(result.current.state.errorMessage).toBe("OAuth flow is not ready yet.");
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it("invalidates account and dashboard queries after device OAuth polling succeeds", async () => {
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
    getOauthStatusMock.mockResolvedValue({
      status: "success",
      errorMessage: null,
    });

    const { queryClient, result } = renderUseOauth();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    await act(async () => {
      await result.current.start("device");
    });

    await act(async () => {
      await result.current.poll();
    });

    expect(getOauthStatusMock).toHaveBeenCalledWith("flow-device");
    expect(result.current.state.status).toBe("success");
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "list"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "trends"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard", "overview"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard", "projections"] });
  });

  it("polls browser OAuth status and invalidates caches after browser success", async () => {
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
    getOauthStatusMock.mockResolvedValue({
      status: "success",
      errorMessage: null,
    });

    const { queryClient, result } = renderUseOauth();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    await act(async () => {
      await result.current.start("browser");
    });

    expect(result.current.state.intervalSeconds).toBe(2);

    await act(async () => {
      await result.current.poll();
    });

    expect(getOauthStatusMock).toHaveBeenCalledWith("flow-browser");
    expect(result.current.state.status).toBe("success");
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "list"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "trends"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard", "overview"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard", "projections"] });
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

    const { queryClient, result } = renderUseOauth();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

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
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "list"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard", "overview"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard", "projections"] });
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

    const { result } = renderUseOauth();

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
