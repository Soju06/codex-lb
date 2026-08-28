import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  completeOauth,
  getOauthStatus,
  startOauth,
  submitManualOauthCallback,
} from "@/features/accounts/api";
import { invalidateAccountRelatedQueries } from "@/features/accounts/query-invalidation";
import { OAuthStateSchema, type OAuthState } from "@/features/accounts/schemas";

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

const DEFAULT_BROWSER_OAUTH_POLL_INTERVAL_SECONDS = 2;

export function useOauth() {
  const queryClient = useQueryClient();
  const [state, setState] = useState<OAuthState>(INITIAL_OAUTH_STATE);
  const stateRef = useRef<OAuthState>(INITIAL_OAUTH_STATE);
  const pollTimerRef = useRef<number | null>(null);
  const countdownTimerRef = useRef<number | null>(null);

  const setOauthState = useCallback((updater: OAuthState | ((current: OAuthState) => OAuthState)) => {
    setState((current) => {
      const next = typeof updater === "function" ? updater(current) : updater;
      stateRef.current = next;
      return next;
    });
  }, []);

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
    setOauthState(INITIAL_OAUTH_STATE);
  }, [clearCountdownTimer, clearPollTimer, setOauthState]);

  const poll = useCallback(async () => {
    try {
      const status = await getOauthStatus(stateRef.current.flowId ?? undefined);
      if (status.status === "success") {
        const response = await completeOauth({
          ...(stateRef.current.flowId ? { flowId: stateRef.current.flowId } : {}),
          deviceAuthId: stateRef.current.deviceAuthId ?? undefined,
          userCode: stateRef.current.userCode ?? undefined,
        });
        setOauthState((prev) =>
          OAuthStateSchema.parse({
            ...prev,
            status: response.status === "success" ? "success" : response.status === "error" ? "error" : "pending",
            errorMessage: response.errorMessage ?? null,
          }),
        );
        if (response.status === "success") {
          invalidateAccountRelatedQueries(queryClient);
          clearPollTimer();
          clearCountdownTimer();
        } else if (response.status === "error") {
          clearPollTimer();
          clearCountdownTimer();
        }
        return;
      }

      setOauthState((prev) =>
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
      if (status.status === "error") {
        clearPollTimer();
        clearCountdownTimer();
      }
    } catch (error) {
      clearPollTimer();
      clearCountdownTimer();
      setOauthState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status: "error",
          errorMessage: error instanceof Error ? error.message : "Failed to poll OAuth status",
        }),
      );
    }
  }, [clearCountdownTimer, clearPollTimer, queryClient, setOauthState]);

  const schedulePollTimer = useCallback((intervalSeconds: number | null) => {
    clearPollTimer();
    if (!intervalSeconds || intervalSeconds <= 0) {
      return;
    }
    pollTimerRef.current = window.setInterval(() => {
      void poll();
    }, intervalSeconds * 1000);
  }, [clearPollTimer, poll]);

  const scheduleCountdownTimer = useCallback((expiresInSeconds: number | null) => {
    clearCountdownTimer();
    if (!expiresInSeconds || expiresInSeconds <= 0) {
      return;
    }
    countdownTimerRef.current = window.setInterval(() => {
      setOauthState((prev) => {
        const expiresInSeconds = Math.max(0, (prev.expiresInSeconds ?? 0) - 1);
        if (expiresInSeconds === 0) {
          clearCountdownTimer();
        }
        return OAuthStateSchema.parse({
          ...prev,
          expiresInSeconds,
        });
      });
    }, 1000);
  }, [clearCountdownTimer, setOauthState]);

  const start = useCallback(async (forceMethod?: "browser" | "device", accountId?: string) => {
    clearPollTimer();
    clearCountdownTimer();
    setOauthState((prev) => ({ ...prev, status: "starting", errorMessage: null }));

    try {
      const response = await startOauth({ forceMethod, accountId });
      const method = response.method === "device" ? "device" : "browser";
      const nextState = OAuthStateSchema.parse({
        flowId: response.flowId ?? null,
        status: "pending",
        method,
        authorizationUrl: response.authorizationUrl,
        callbackUrl: response.callbackUrl,
        verificationUrl: response.verificationUrl,
        userCode: response.userCode,
        deviceAuthId: response.deviceAuthId,
        intervalSeconds:
          response.intervalSeconds
          ?? (method === "browser" && response.flowId ? DEFAULT_BROWSER_OAUTH_POLL_INTERVAL_SECONDS : null),
        expiresInSeconds: response.expiresInSeconds,
        errorMessage: null,
      });
      setOauthState(nextState);
      schedulePollTimer(nextState.intervalSeconds);
      scheduleCountdownTimer(nextState.expiresInSeconds);

      if (
        nextState.method === "device"
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
      clearPollTimer();
      clearCountdownTimer();
      setOauthState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status: "error",
          errorMessage: message,
        }),
      );
      throw error;
    }
  }, [clearCountdownTimer, clearPollTimer, scheduleCountdownTimer, schedulePollTimer, setOauthState]);

  const complete = useCallback(async () => {
    try {
      const response = await completeOauth({
        ...(stateRef.current.flowId ? { flowId: stateRef.current.flowId } : {}),
        deviceAuthId: stateRef.current.deviceAuthId ?? undefined,
        userCode: stateRef.current.userCode ?? undefined,
      });
      setOauthState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status: response.status === "success" ? "success" : response.status === "error" ? "error" : "pending",
          errorMessage: response.errorMessage ?? null,
        }),
      );
      if (response.status === "success") {
        invalidateAccountRelatedQueries(queryClient);
        clearPollTimer();
        clearCountdownTimer();
      } else if (response.status === "error") {
        clearPollTimer();
        clearCountdownTimer();
      }
    } catch (error) {
      clearPollTimer();
      clearCountdownTimer();
      setOauthState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status: "error",
          errorMessage: error instanceof Error ? error.message : "Failed to complete OAuth",
        }),
      );
      throw error;
    }
  }, [clearCountdownTimer, clearPollTimer, queryClient, setOauthState]);

  const manualCallback = useCallback(async (callbackUrl: string) => {
    try {
      const response = await submitManualOauthCallback({
        callbackUrl,
        ...(stateRef.current.flowId ? { flowId: stateRef.current.flowId } : {}),
      });
      setOauthState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status: response.status === "success" ? "success" : "error",
          errorMessage: response.errorMessage,
        }),
      );
      if (response.status === "success") {
        invalidateAccountRelatedQueries(queryClient);
        clearPollTimer();
        clearCountdownTimer();
      } else {
        clearPollTimer();
        clearCountdownTimer();
      }
      return response;
    } catch (error) {
      clearPollTimer();
      clearCountdownTimer();
      setOauthState((prev) =>
        OAuthStateSchema.parse({
          ...prev,
          status: "error",
          errorMessage: error instanceof Error ? error.message : "Failed to process OAuth callback",
        }),
      );
      throw error;
    }
  }, [clearCountdownTimer, clearPollTimer, queryClient, setOauthState]);

  useEffect(() => {
    return () => {
      clearPollTimer();
      clearCountdownTimer();
    };
  }, [clearCountdownTimer, clearPollTimer]);

  return {
    state,
    start,
    poll,
    complete,
    manualCallback,
    reset,
  };
}
