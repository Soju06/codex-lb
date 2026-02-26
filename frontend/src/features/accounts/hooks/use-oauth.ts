import { useCallback, useEffect, useRef, useState } from "react";

import { completeOauth, getOauthStatus, startOauth } from "@/features/accounts/api";
import { OAuthStateSchema, type OAuthState } from "@/features/accounts/schemas";

const INITIAL_OAUTH_STATE: OAuthState = OAuthStateSchema.parse({
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

export function useOauth() {
  const [state, setState] = useState<OAuthState>(INITIAL_OAUTH_STATE);
  const pollTimerRef = useRef<number | null>(null);
  const countdownTimerRef = useRef<number | null>(null);

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
    setState(INITIAL_OAUTH_STATE);
  }, [clearCountdownTimer, clearPollTimer]);

  const poll = useCallback(async () => {
    try {
      const status = await getOauthStatus();
      setState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status:
            status.status === "success"
              ? "success"
              : status.status === "error"
                ? "error"
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
  }, []);

  const start = useCallback(async (forceMethod?: "browser" | "device") => {
    clearPollTimer();
    clearCountdownTimer();
    setState((prev) => ({ ...prev, status: "starting", errorMessage: null }));

    try {
      const response = await startOauth({ forceMethod });
      const nextState = OAuthStateSchema.parse({
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

      if (
        nextState.method === "device"
        && nextState.deviceAuthId
        && nextState.userCode
      ) {
        await completeOauth({
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
  }, [clearCountdownTimer, clearPollTimer]);

  const complete = useCallback(async () => {
    try {
      await completeOauth({
        deviceAuthId: state.deviceAuthId ?? undefined,
        userCode: state.userCode ?? undefined,
      });
      setState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status: "success",
        }),
      );
    } catch (error) {
      setState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status: "error",
          errorMessage: error instanceof Error ? error.message : "Failed to complete OAuth",
        }),
      );
      throw error;
    }
  }, [state.deviceAuthId, state.userCode]);

  useEffect(() => {
    if (state.status !== "pending" || !state.intervalSeconds || state.intervalSeconds <= 0) {
      clearPollTimer();
      return;
    }
    clearPollTimer();
    pollTimerRef.current = window.setInterval(() => {
      void poll();
    }, state.intervalSeconds * 1000);
    return clearPollTimer;
  }, [clearPollTimer, poll, state.intervalSeconds, state.status]);

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
    if (state.status === "success" || state.status === "error") {
      clearPollTimer();
      clearCountdownTimer();
    }
  }, [clearCountdownTimer, clearPollTimer, state.status]);

  return {
    state,
    start,
    poll,
    complete,
    reset,
  };
}
