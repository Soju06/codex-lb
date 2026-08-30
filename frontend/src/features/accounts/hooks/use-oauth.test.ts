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

function createDeferred<T>(): {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
  readonly reject: (reason?: unknown) => void;
} {
  let resolve: ((value: T) => void) | undefined;
  let reject: ((reason?: unknown) => void) | undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  if (resolve === undefined || reject === undefined) {
    throw new Error("deferred executor did not run");
  }
  return { promise, resolve, reject };
}

function browserOauthStart(flowId: string) {
  return {
    flowId,
    method: "browser",
    authorizationUrl: `https://auth.example.com/authorize?flow=${flowId}`,
    callbackUrl: "http://127.0.0.1:1455/auth/callback",
    verificationUrl: null,
    userCode: null,
    deviceAuthId: null,
    intervalSeconds: 3600,
    expiresInSeconds: 600,
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
    completeOauthMock
      .mockResolvedValueOnce({ status: "pending" })
      .mockResolvedValueOnce({ status: "success" });

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

  it("passes the selected local account id for targeted reauthentication", async () => {
    startOauthMock.mockResolvedValue({
      flowId: "flow-reauth",
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
      await result.current.start("browser", "team-seat-a");
    });

    expect(startOauthMock).toHaveBeenCalledWith({
      forceMethod: "browser",
      accountId: "team-seat-a",
    });
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
    completeOauthMock.mockResolvedValue({ status: "success" });

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

  it("stops polling when browser OAuth completion returns an error", async () => {
    vi.useFakeTimers();
    try {
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
      completeOauthMock.mockResolvedValue({
        status: "error",
        errorMessage: "OAuth completion failed",
      });

      const { result } = renderUseOauth();

      await act(async () => {
        await result.current.start("browser");
      });
      await act(async () => {
        await result.current.poll();
      });

      expect(result.current.state.status).toBe("error");
      expect(completeOauthMock).toHaveBeenCalledTimes(1);

      await act(async () => {
        vi.advanceTimersByTime(2_000);
      });

      expect(completeOauthMock).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
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

  it("stops browser polling and countdown after a failed manual callback", async () => {
    vi.useFakeTimers();
    try {
      startOauthMock.mockResolvedValue({
        flowId: "flow-browser",
        method: "browser",
        authorizationUrl: "https://auth.example.com/authorize",
        callbackUrl: "http://127.0.0.1:1455/auth/callback",
        verificationUrl: null,
        userCode: null,
        deviceAuthId: null,
        intervalSeconds: null,
        expiresInSeconds: 60,
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
      expect(result.current.state.expiresInSeconds).toBe(60);

      await act(async () => {
        vi.advanceTimersByTime(2_000);
      });

      expect(getOauthStatusMock).not.toHaveBeenCalled();
      expect(result.current.state.expiresInSeconds).toBe(60);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops the countdown timer when OAuth expires", async () => {
    vi.useFakeTimers();
    try {
      startOauthMock.mockResolvedValue({
        flowId: "flow-browser",
        method: "browser",
        authorizationUrl: "https://auth.example.com/authorize",
        callbackUrl: "http://127.0.0.1:1455/auth/callback",
        verificationUrl: null,
        userCode: null,
        deviceAuthId: null,
        intervalSeconds: null,
        expiresInSeconds: 1,
      });

      const { result } = renderUseOauth();

      await act(async () => {
        await result.current.start("browser");
      });
      expect(result.current.state.expiresInSeconds).toBe(1);

      await act(async () => {
        vi.advanceTimersByTime(1_000);
      });
      expect(result.current.state.expiresInSeconds).toBe(0);

      await act(async () => {
        vi.advanceTimersByTime(5_000);
      });
      expect(result.current.state.expiresInSeconds).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not complete or mutate flow B when a stale flow A poll succeeds after restart", async () => {
    const statusA = createDeferred<{ status: string; errorMessage: null }>();
    startOauthMock
      .mockResolvedValueOnce(browserOauthStart("flow-a"))
      .mockResolvedValueOnce(browserOauthStart("flow-b"));
    completeOauthMock.mockResolvedValue({ status: "success", errorMessage: null });
    getOauthStatusMock.mockImplementation((flowId: unknown) => {
      if (flowId === "flow-a") {
        return statusA.promise;
      }
      return Promise.resolve({ status: "pending", errorMessage: null });
    });

    const { queryClient, result } = renderUseOauth();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    await act(async () => {
      await result.current.start("browser");
    });
    expect(result.current.state.flowId).toBe("flow-a");
    expect(result.current.state.status).toBe("pending");

    let pollA: Promise<void> | undefined;
    act(() => {
      pollA = result.current.poll();
    });
    if (pollA === undefined) {
      throw new Error("poll A did not start");
    }
    expect(getOauthStatusMock).toHaveBeenCalledWith("flow-a");

    await act(async () => {
      result.current.reset();
      await result.current.start("browser");
    });
    expect(result.current.state.flowId).toBe("flow-b");
    expect(result.current.state.status).toBe("pending");
    expect(result.current.state.authorizationUrl).toBe(
      "https://auth.example.com/authorize?flow=flow-b",
    );

    const pendingPollA = pollA;
    await act(async () => {
      statusA.resolve({ status: "success", errorMessage: null });
      await pendingPollA;
    });

    expect(completeOauthMock).not.toHaveBeenCalled();
    expect(invalidateSpy).not.toHaveBeenCalled();
    expect(result.current.state.flowId).toBe("flow-b");
    expect(result.current.state.status).toBe("pending");
    expect(result.current.state.errorMessage).toBeNull();
    expect(result.current.state.authorizationUrl).toBe(
      "https://auth.example.com/authorize?flow=flow-b",
    );
  });

  it("does not write a stale flow A poll error onto flow B after restart", async () => {
    const statusA = createDeferred<{ status: string; errorMessage: string }>();
    startOauthMock
      .mockResolvedValueOnce(browserOauthStart("flow-a"))
      .mockResolvedValueOnce(browserOauthStart("flow-b"));
    getOauthStatusMock.mockImplementation((flowId: unknown) => {
      if (flowId === "flow-a") {
        return statusA.promise;
      }
      return Promise.resolve({ status: "pending", errorMessage: null });
    });

    const { result } = renderUseOauth();

    await act(async () => {
      await result.current.start("browser");
    });

    let pollA: Promise<void> | undefined;
    act(() => {
      pollA = result.current.poll();
    });
    if (pollA === undefined) {
      throw new Error("poll A did not start");
    }

    await act(async () => {
      result.current.reset();
      await result.current.start("browser");
    });
    expect(result.current.state.flowId).toBe("flow-b");
    expect(result.current.state.status).toBe("pending");

    const pendingPollA = pollA;
    await act(async () => {
      statusA.resolve({ status: "error", errorMessage: "stale flow A failed" });
      await pendingPollA;
    });

    expect(completeOauthMock).not.toHaveBeenCalled();
    expect(result.current.state.flowId).toBe("flow-b");
    expect(result.current.state.status).toBe("pending");
    expect(result.current.state.errorMessage).toBeNull();
  });

  it("does not apply a stale start success onto flow B after restart", async () => {
    const startA = createDeferred<ReturnType<typeof browserOauthStart>>();
    startOauthMock
      .mockImplementationOnce(() => startA.promise)
      .mockResolvedValueOnce(browserOauthStart("flow-b"));

    const { result } = renderUseOauth();

    let pendingStartA: Promise<unknown> | undefined;
    act(() => {
      pendingStartA = result.current.start("browser");
    });
    if (pendingStartA === undefined) {
      throw new Error("start A did not begin");
    }
    expect(result.current.state.status).toBe("starting");

    await act(async () => {
      result.current.reset();
      await result.current.start("browser");
    });
    expect(result.current.state.flowId).toBe("flow-b");
    expect(result.current.state.status).toBe("pending");

    const startedA = pendingStartA;
    await act(async () => {
      startA.resolve(browserOauthStart("flow-a"));
      await startedA;
    });

    expect(result.current.state.flowId).toBe("flow-b");
    expect(result.current.state.status).toBe("pending");
    expect(result.current.state.authorizationUrl).toBe(
      "https://auth.example.com/authorize?flow=flow-b",
    );
    expect(result.current.state.errorMessage).toBeNull();
  });

  it("does not apply a stale start error onto flow B after restart", async () => {
    const startA = createDeferred<ReturnType<typeof browserOauthStart>>();
    startOauthMock
      .mockImplementationOnce(() => startA.promise)
      .mockResolvedValueOnce(browserOauthStart("flow-b"));

    const { result } = renderUseOauth();

    let pendingStartA: Promise<unknown> | undefined;
    act(() => {
      pendingStartA = result.current.start("browser");
    });
    if (pendingStartA === undefined) {
      throw new Error("start A did not begin");
    }

    await act(async () => {
      result.current.reset();
      await result.current.start("browser");
    });
    expect(result.current.state.flowId).toBe("flow-b");
    expect(result.current.state.status).toBe("pending");

    const startedA = pendingStartA;
    let startAError: unknown;
    await act(async () => {
      startA.reject(new Error("stale start failed"));
      startAError = await startedA.then(
        () => undefined,
        (error: unknown) => error,
      );
    });
    expect(startAError).toBeInstanceOf(Error);

    expect(result.current.state.flowId).toBe("flow-b");
    expect(result.current.state.status).toBe("pending");
    expect(result.current.state.errorMessage).toBeNull();
  });
});
