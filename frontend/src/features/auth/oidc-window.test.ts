import { afterEach, describe, expect, it, vi } from "vitest";

import {
  closeFlowWindowOnReturn,
  OIDC_FLOW_RETURN_CHANNEL,
  OIDC_FLOW_RETURN_MESSAGE,
  OIDC_FLOW_WINDOW_NAME,
  rememberFlow,
  signedInLoginDestination,
  takeFlow,
  watchFlowWindow,
} from "@/features/auth/oidc-window";
import { ORGANISATION_SETTINGS_RETURN_URL } from "@/features/settings/advanced-settings-deeplink";

const PROVIDER_ID = "provider_oidc";

/** A broadcast is delivered on a later task, so give it one. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

describe("the sign-in flow window", () => {
  afterEach(() => {
    window.sessionStorage.clear();
    window.name = "";
    Object.defineProperty(window, "opener", { value: null, configurable: true, writable: true });
    vi.restoreAllMocks();
  });

  it("carries an id and two timestamps across a same-tab round trip, and nothing else", () => {
    rememberFlow({ providerId: PROVIDER_ID, purpose: "test-login", verifiedAt: null });

    const raw = window.sessionStorage.getItem("codex-lb.oidc-flow") ?? "";
    expect(JSON.parse(raw)).toEqual({ providerId: PROVIDER_ID, purpose: "test-login", verifiedAt: null });
    expect(raw).not.toMatch(/secret|client|issuer/i);
  });

  it("is single use, and answers nothing to a second reader", () => {
    rememberFlow({ providerId: PROVIDER_ID, purpose: "test-login", verifiedAt: "2026-02-01T00:00:00Z" });

    expect(takeFlow()).toEqual({
      providerId: PROVIDER_ID,
      purpose: "test-login",
      verifiedAt: "2026-02-01T00:00:00Z",
    });
    expect(takeFlow()).toBeNull();
  });

  it("refuses a marker that is not one", () => {
    window.sessionStorage.setItem("codex-lb.oidc-flow", "{not json");
    expect(takeFlow()).toBeNull();
    window.sessionStorage.setItem("codex-lb.oidc-flow", JSON.stringify({ providerId: PROVIDER_ID }));
    expect(takeFlow()).toBeNull();
  });

  it("takes a return message only from the window it opened, at its own origin", () => {
    const handle = { closed: false } as unknown as Window;
    const ended = vi.fn();
    const stop = watchFlowWindow(handle, ended);

    const post = (init: MessageEventInit) =>
      window.dispatchEvent(new MessageEvent("message", { data: { type: OIDC_FLOW_RETURN_MESSAGE }, ...init }));

    post({ origin: "https://elsewhere.example.com", source: handle });
    post({ origin: window.location.origin, source: null });
    window.dispatchEvent(
      new MessageEvent("message", { data: { type: "something-else" }, origin: window.location.origin, source: handle }),
    );
    expect(ended).not.toHaveBeenCalled();

    post({ origin: window.location.origin, source: handle });
    expect(ended).toHaveBeenCalledTimes(1);

    // One end per flow: the closed-poll backstop must not report it a second time.
    post({ origin: window.location.origin, source: handle });
    expect(ended).toHaveBeenCalledTimes(1);
    stop();
  });

  it("keeps waiting when the handle says it is closed, because a severed one says exactly that", () => {
    vi.useFakeTimers();
    try {
      // An identity provider serving `Cross-Origin-Opener-Policy: same-origin`
      // on its authorization endpoint takes the opener link away the moment the
      // window commits that page, and from then on the handle reports a window
      // that is standing wide open as closed.
      const handle = { closed: true } as unknown as Window;
      const ended = vi.fn();
      const stop = watchFlowWindow(handle, ended);

      vi.advanceTimersByTime(600);
      expect(ended.mock.calls).toEqual([["closed"]]);

      // And it says it once: the poll gives up on a handle it cannot trust
      // rather than re-reading twice a second for the rest of the flow.
      vi.advanceTimersByTime(60_000);
      expect(ended.mock.calls).toEqual([["closed"]]);

      // A minute later the person finishes signing in and the window comes
      // back. The watch is still there to hear it.
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: OIDC_FLOW_RETURN_MESSAGE },
          origin: window.location.origin,
          source: handle,
        }),
      );
      expect(ended.mock.calls).toEqual([["closed"], ["returned"]]);
      stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it("takes the same nudge from the channel the returning window broadcasts on", async () => {
    const handle = { closed: false } as unknown as Window;
    const ended = vi.fn();
    const stop = watchFlowWindow(handle, ended);

    const channel = new BroadcastChannel(OIDC_FLOW_RETURN_CHANNEL);
    channel.postMessage({ type: "something-else" });
    await settle();
    expect(ended).not.toHaveBeenCalled();

    channel.postMessage({ type: OIDC_FLOW_RETURN_MESSAGE });
    await settle();
    expect(ended.mock.calls).toEqual([["returned"]]);
    channel.close();
    stop();
  });

  it("stops watching once the flow itself has expired", () => {
    vi.useFakeTimers();
    try {
      const handle = { closed: false } as unknown as Window;
      const ended = vi.fn();
      const stop = watchFlowWindow(handle, ended);

      // Nine minutes of an open window is still a flow that can complete.
      vi.advanceTimersByTime(9 * 60_000);
      expect(ended).not.toHaveBeenCalled();

      // Past the server's ten-minute flow lifetime nothing can arrive, so the
      // watch ends the way an abandoned one does: one re-read, not a spinner.
      vi.advanceTimersByTime(60_000);
      expect(ended).toHaveBeenCalledTimes(1);

      vi.advanceTimersByTime(10 * 60_000);
      expect(ended).toHaveBeenCalledTimes(1);
      stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it("sends a signed-in session on the login route to the dashboard, or back to the card it left", () => {
    expect(signedInLoginDestination()).toBe("/dashboard");

    // Only a marker this module wrote counts; leftovers under the same key do not.
    window.sessionStorage.setItem("codex-lb.oidc-flow", JSON.stringify({ providerId: PROVIDER_ID }));
    expect(signedInLoginDestination()).toBe("/dashboard");

    rememberFlow({ providerId: PROVIDER_ID, purpose: "test-login", verifiedAt: null });
    expect(signedInLoginDestination()).toBe(ORGANISATION_SETTINGS_RETURN_URL);
    // Looked at, never spent: the card is what consumes the marker.
    expect(takeFlow()).not.toBeNull();
    expect(signedInLoginDestination()).toBe("/dashboard");
  });

  it("stops watching when the caller does", () => {
    const handle = { closed: true } as unknown as Window;
    const ended = vi.fn();
    watchFlowWindow(handle, ended)();

    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: OIDC_FLOW_RETURN_MESSAGE },
        origin: window.location.origin,
        source: handle,
      }),
    );
    expect(ended).not.toHaveBeenCalled();
  });

  it("only the flow window itself reports back and closes", () => {
    const opener = { postMessage: vi.fn() };
    const close = vi.spyOn(window, "close").mockImplementation(() => {});
    Object.defineProperty(window, "opener", { value: opener, configurable: true, writable: true });

    // An ordinary tab that happens to have an opener is not a flow window.
    window.history.replaceState({}, "", "/settings?org=1#oidc");
    window.name = "";
    expect(closeFlowWindowOnReturn()).toBe(false);

    // Neither is a flow window parked somewhere the server never returns one to.
    window.name = OIDC_FLOW_WINDOW_NAME;
    window.history.replaceState({}, "", "/dashboard");
    expect(closeFlowWindowOnReturn()).toBe(false);

    window.history.replaceState({}, "", "/settings?org=1#oidc");
    expect(closeFlowWindowOnReturn()).toBe(true);
    // The message says the round trip ended. It carries no verdict.
    expect(opener.postMessage).toHaveBeenCalledWith({ type: OIDC_FLOW_RETURN_MESSAGE }, window.location.origin);
    expect(close).toHaveBeenCalled();
  });

  it("still reports the round trip when the identity provider took the opener away", async () => {
    // What an identity provider serving `Cross-Origin-Opener-Policy:
    // same-origin` leaves behind: the window is back on this origin, but its
    // opener is gone for good — the return navigation switches browsing
    // context group a second time rather than handing it back.
    const close = vi.spyOn(window, "close").mockImplementation(() => {});
    Object.defineProperty(window, "opener", { value: null, configurable: true, writable: true });
    window.name = OIDC_FLOW_WINDOW_NAME;
    window.history.replaceState({}, "", "/settings?org=1#oidc");

    const handle = { closed: true } as unknown as Window;
    const ended = vi.fn();
    const stop = watchFlowWindow(handle, ended);

    // The document is not handled here: a window disowned that way is not
    // script-closable everywhere, and a second copy of Settings is a better
    // worst case than a blank window nothing was ever mounted into.
    expect(closeFlowWindowOnReturn()).toBe(false);
    expect(close).not.toHaveBeenCalled();

    await settle();
    expect(ended.mock.calls).toEqual([["returned"]]);
    stop();
  });
});
