import { useCallback, useEffect, useRef, useState } from "react";

import {
  completeOauth,
  getOauthStatus,
  resetOauth,
  startOauth,
  submitManualOauthCallback,
} from "@/features/accounts/api";
import {
  OAuthStateSchema,
  type AccountProxyInput,
  type OAuthState,
} from "@/features/accounts/schemas";

const INITIAL_OAUTH_STATE: OAuthState = OAuthStateSchema.parse({
  flowId: null,
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
});
const BROWSER_POLL_INTERVAL_SECONDS = 2;
const DEVICE_POLL_INTERVAL_SECONDS = 5;

export function useOauth() {
  const [state, setState] = useState<OAuthState>(INITIAL_OAUTH_STATE);
  const pollTimerRef = useRef<number | null>(null);
  const countdownTimerRef = useRef<number | null>(null);
  const resetInFlightRef = useRef<Promise<void> | null>(null);

  const clearPollTimer = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const clearCountdownTimer = useCallback(() => {
    if (countdownTimerRef.current !== null) {
      window.clearInterval(countdownTimerRef.current);
      countdownTimerRef.current = null;
    }
  }, []);

  const reset = useCallback(() => {
    clearPollTimer();
    clearCountdownTimer();
    const resetPromise = resetOauth()
      .catch(() => undefined)
      .then(() => undefined);
    resetInFlightRef.current = resetPromise;
    void resetPromise.finally(() => {
      if (resetInFlightRef.current === resetPromise) {
        resetInFlightRef.current = null;
      }
    });
    setState(INITIAL_OAUTH_STATE);
    return resetPromise;
  }, [clearCountdownTimer, clearPollTimer]);

  const poll = useCallback(async () => {
    try {
      const status = await getOauthStatus(state.flowId ?? undefined);
      setState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status:
            status.status === "success"
              ? "success"
              : status.status === "error"
                ? "error"
                : status.status === "tokens_ready"
                  ? "tokens_ready"
                  : "pending",
          errorMessage: status.errorMessage,
        }),
      );
    } catch (error) {
      setState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status: "error",
          errorMessage: error instanceof Error ? error.message : "Failed to poll OAuth status",
        }),
      );
    }
  }, [state.flowId]);

  const start = useCallback(
    async (
      forceMethod?: "browser" | "device",
      options?: { expectProxy?: boolean; proxy?: AccountProxyInput },
    ) => {
      clearPollTimer();
      clearCountdownTimer();
      setState((prev) => ({ ...prev, status: "starting", errorMessage: null }));

      try {
        if (resetInFlightRef.current) {
          await resetInFlightRef.current;
        }
        const expectProxy = Boolean(options?.expectProxy);
        const proxy = options?.proxy;
        const response = await startOauth({
          forceMethod,
          expectProxy,
          ...(proxy
            ? {
                proxyHost: proxy.host,
                proxyPort: proxy.port,
                proxyUsername: proxy.username ?? undefined,
                proxyPassword: proxy.password ?? undefined,
                proxyRemoteDns: proxy.remoteDns,
                proxyLabel: proxy.label ?? undefined,
              }
            : {}),
        });
        const nextState = OAuthStateSchema.parse({
          flowId: response.flowId ?? null,
          status: "pending",
          method: response.method === "device" ? "device" : "browser",
          authorizationUrl: response.authorizationUrl,
          callbackUrl: response.callbackUrl,
          verificationUrl: response.verificationUrl,
          userCode: response.userCode,
          deviceAuthId: response.deviceAuthId,
          intervalSeconds: response.intervalSeconds,
          expiresInSeconds: response.expiresInSeconds,
          errorMessage: null,
        });
        setState(nextState);

        // Device flow: a no-op /complete call keeps compatibility with
        // the non-proxy path. In expectProxy mode the backend starts
        // polling from /start, and /complete is reserved for the final
        // proxy-bearing persist step.
        if (
          !expectProxy
          && nextState.method === "device"
          && nextState.deviceAuthId
          && nextState.userCode
        ) {
          await completeOauth({
            ...(nextState.flowId ? { flowId: nextState.flowId } : {}),
            deviceAuthId: nextState.deviceAuthId,
            userCode: nextState.userCode,
          });
        }

        return nextState;
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to start OAuth";
        setState((prev) =>
          OAuthStateSchema.parse({
            ...prev,
            status: "error",
            errorMessage: message,
          }),
        );
        throw error;
      }
    },
    [clearCountdownTimer, clearPollTimer],
  );

  const complete = useCallback(
    async (proxy?: AccountProxyInput) => {
      try {
        const response = await completeOauth({
          ...(state.flowId ? { flowId: state.flowId } : {}),
          deviceAuthId: state.deviceAuthId ?? undefined,
          userCode: state.userCode ?? undefined,
          ...(proxy
            ? {
                proxyHost: proxy.host,
                proxyPort: proxy.port,
                proxyUsername: proxy.username ?? undefined,
                proxyPassword: proxy.password ?? undefined,
                proxyRemoteDns: proxy.remoteDns,
                proxyLabel: proxy.label ?? undefined,
              }
            : {}),
        });
        if (response.status !== "success") {
          const message =
            response.status === "pending"
              ? "OAuth is still pending"
              : "Failed to complete OAuth";
          setState((prev) =>
            OAuthStateSchema.parse({
              ...prev,
              status: response.status === "tokens_ready" ? "tokens_ready" : "error",
              errorMessage: message,
            }),
          );
          throw new Error(message);
        }
        setState((prev) =>
          OAuthStateSchema.parse({
            ...prev,
            status: "success",
            errorMessage: null,
          }),
        );
      } catch (error) {
        setState((prev) =>
          OAuthStateSchema.parse({
            ...prev,
            status: prev.status === "tokens_ready" ? "tokens_ready" : "error",
            errorMessage: error instanceof Error ? error.message : "Failed to complete OAuth",
          }),
        );
        throw error;
      }
    },
    [state.deviceAuthId, state.flowId, state.userCode],
  );

  const manualCallback = useCallback(async (callbackUrl: string) => {
    try {
      const response = await submitManualOauthCallback({
        callbackUrl,
        ...(state.flowId ? { flowId: state.flowId } : {}),
      });
      setState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status:
            response.status === "success"
              ? "success"
              : response.status === "tokens_ready"
                ? "tokens_ready"
                : "error",
          errorMessage: response.errorMessage,
        }),
      );
      return response;
    } catch (error) {
      setState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status: "error",
          errorMessage: error instanceof Error ? error.message : "Failed to process OAuth callback",
        }),
      );
      throw error;
    }
  }, [state.flowId]);

  useEffect(() => {
    if (state.status !== "pending") {
      clearPollTimer();
      return;
    }
    clearPollTimer();
    const intervalSeconds =
      state.intervalSeconds && state.intervalSeconds > 0
        ? state.intervalSeconds
        : state.method === "browser"
          ? BROWSER_POLL_INTERVAL_SECONDS
          : state.method === "device"
            ? DEVICE_POLL_INTERVAL_SECONDS
            : null;
    if (intervalSeconds === null) {
      return;
    }
    pollTimerRef.current = window.setInterval(() => {
      void poll();
    }, intervalSeconds * 1000);
    return clearPollTimer;
  }, [clearPollTimer, poll, state.intervalSeconds, state.method, state.status]);

  useEffect(() => {
    if (state.status !== "pending" || !state.expiresInSeconds || state.expiresInSeconds <= 0) {
      clearCountdownTimer();
      return;
    }
    clearCountdownTimer();
    countdownTimerRef.current = window.setInterval(() => {
      setState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          expiresInSeconds: Math.max(0, (prev.expiresInSeconds ?? 0) - 1),
        }),
      );
    }, 1000);
    return clearCountdownTimer;
  }, [clearCountdownTimer, state.expiresInSeconds, state.status]);

  useEffect(() => {
    if (
      state.status === "success"
      || state.status === "error"
      || state.status === "tokens_ready"
    ) {
      clearPollTimer();
      clearCountdownTimer();
    }
  }, [clearCountdownTimer, clearPollTimer, state.status]);

  return {
    state,
    start,
    poll,
    complete,
    manualCallback,
    reset,
  };
}
