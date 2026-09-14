// The browser window a sign-in round trip runs in, and the one thing it is
// allowed to tell the page that opened it.
//
// The OIDC callback answers a `303` and nothing else: no JSON, no verdict, no
// state on the return URL. So a pre-flight started from Settings has no
// first-party completion signal at all, and everything here exists to turn
// "the window came back" into "ask the server again" — never into an answer.

import { ORGANISATION_SETTINGS_RETURN_URL } from "@/features/settings/advanced-settings-deeplink";

/**
 * The name the flow window carries. It is what lets the returning page know it
 * is a flow window rather than an ordinary tab somebody opened from a link, and
 * it is set by `window.open` in the activation handler.
 */
export const OIDC_FLOW_WINDOW_NAME = "codex-lb-oidc-flow";

/** The message the returning window posts to its opener. It carries no verdict. */
export const OIDC_FLOW_RETURN_MESSAGE = "codex-lb.oidc-flow-return";

/**
 * The same-origin channel the returning window announces itself on, alongside
 * the message to its opener.
 *
 * An identity provider that serves `Cross-Origin-Opener-Policy: same-origin` on
 * its authorization endpoint — Entra ID, the very provider this card exists to
 * connect, shipped exactly that on `/authorize` — forces a browsing-context-group
 * switch when the window commits that document. The opener link does not come
 * back when the window returns to this origin: a second switch happens instead.
 * So `window.opener` is null here for good, and in the page that opened it the
 * handle reports `closed` on a window that is standing wide open.
 *
 * A broadcast needs neither. It is same-origin by construction — there is
 * nothing to check about where it came from, because nothing else can send one
 * — and the window is back on this origin by the time it sends one. Like the
 * message to the opener it is a nudge to ask the server again, never an answer.
 */
export const OIDC_FLOW_RETURN_CHANNEL = "codex-lb.oidc-flow-window";

/** The two in-app paths the server returns a flow to (its settings and failure destinations). */
const FLOW_RETURN_PATHS = new Set(["/settings", "/login"]);

const MARKER_KEY = "codex-lb.oidc-flow";
const CLOSED_POLL_MS = 500;
/**
 * How long one flow is watched for. It is the server's own flow lifetime
 * (`OIDC_FLOW_TTL_SECONDS`): past it the stored flow is gone, so a window still
 * standing open at the identity provider can no longer complete anything, and
 * watching it further would be waiting for an answer that cannot arrive.
 */
const FLOW_WATCH_LIMIT_MS = 600_000;

/** The dashboard, where a signed-in session on the login route ordinarily belongs. */
const DASHBOARD_ROUTE = "/dashboard";

export type OidcFlowPurpose = "test-login";

/**
 * What a same-tab flow leaves behind so the returning page can recognise its
 * own round trip: an id, a purpose and the stamp observed before starting.
 * Never the connection document, and never the client secret.
 */
export type OidcFlowMarker = { providerId: string; purpose: OidcFlowPurpose; verifiedAt: string | null };

/**
 * Opened synchronously in the activation handler, empty, and pointed at the
 * authorization URL once the start request answers. A window opened after that
 * `await` — the start is itself step-up gated, so a dialog can interpose — is
 * blocked by every browser.
 */
export function openFlowWindow(): Window | null {
  try {
    return window.open("", OIDC_FLOW_WINDOW_NAME, "popup,width=520,height=680");
  } catch {
    return null;
  }
}

/** The same-tab fallback, behind a seam because jsdom performs no navigation. */
export const flowNavigation = {
  go(url: string): void {
    window.location.assign(url);
  },
};

export function rememberFlow(marker: OidcFlowMarker): void {
  try {
    window.sessionStorage.setItem(MARKER_KEY, JSON.stringify(marker));
  } catch {
    /* A browser that refuses storage still completes the flow; only the return is dumber. */
  }
}

function peekMarker(): string | null {
  try {
    return window.sessionStorage.getItem(MARKER_KEY);
  } catch {
    return null;
  }
}

function readMarker(): string | null {
  const raw = peekMarker();
  try {
    window.sessionStorage.removeItem(MARKER_KEY);
  } catch {
    /* Nothing was readable either, so there is nothing to consume. */
  }
  return raw;
}

function parseMarker(raw: string | null): OidcFlowMarker | null {
  if (raw === null) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) {
      return null;
    }
    const { providerId, purpose, verifiedAt } = parsed as Partial<OidcFlowMarker>;
    if (typeof providerId !== "string" || purpose !== "test-login") {
      return null;
    }
    return { providerId, purpose, verifiedAt: typeof verifiedAt === "string" ? verifiedAt : null };
  } catch {
    return null;
  }
}

/**
 * Single use: a marker that survived its return would re-arm a later visit.
 *
 * Read once at mount. Reading it a second time answers `null`, which fails
 * closed — the operator runs the test login again — and never the other way.
 */
export function takeFlow(): OidcFlowMarker | null {
  return parseMarker(readMarker());
}

/**
 * Where a signed-in session that landed on the login route belongs.
 *
 * Ordinarily the dashboard. But the server ends every *failed* sign-in flow at
 * the login screen, and a same-tab pre-flight is the one flow whose failure
 * arrives there with the operator still signed in: sending them to the
 * dashboard would strand them a page away from the card that started it, with
 * nothing said about what happened. The marker is only looked at here — the
 * card is what consumes it, and what asks the server for the verdict.
 */
export function signedInLoginDestination(): string {
  return parseMarker(peekMarker()) === null ? DASHBOARD_ROUTE : ORGANISATION_SETTINGS_RETURN_URL;
}

/**
 * Why a watch asked its caller to re-read.
 *
 * `returned` is the window itself saying the round trip is over and `expired`
 * the flow's own lifetime running out; both are terminal, and after either the
 * server has nothing further to say about this flow. `closed` is the handle
 * reporting itself closed, which is the one signal that proves nothing: an
 * identity provider serving `Cross-Origin-Opener-Policy: same-origin` disowns
 * the handle the moment the window commits its authorization page, and a
 * disowned handle reports `closed` while the person is still typing their
 * password. It is worth a re-read — a window genuinely closed by hand may have
 * completed first — and worth nothing else, so the watch keeps waiting.
 */
export type OidcFlowEnd = "returned" | "closed" | "expired";

function openReturnChannel(): BroadcastChannel | null {
  try {
    return typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel(OIDC_FLOW_RETURN_CHANNEL);
  } catch {
    /* A browser without the channel still completes the flow through its opener. */
    return null;
  }
}

/**
 * Watches one flow window for the things that can end it: the page it returns
 * to says so, the flow's own lifetime runs out, or the handle reports itself
 * closed. Each calls `onEnded`, which re-reads the provider row — a window that
 * died on the identity provider's own error page, one closed by hand and one
 * that completed all converge there.
 *
 * Only the first two are evidence, and only they end the watch. A handle that
 * reports `closed` may have been severed rather than closed, so the poll clears
 * itself — the handle is untrustworthy from then on either way — and leaves the
 * return signals and the deadline standing, so the round trip that is still
 * running can still land.
 *
 * The watch is bounded by the flow's own ten minutes. A window left standing at
 * the identity provider past that cannot complete anything — the server's flow
 * record is gone — so it ends the same way an abandoned one does, with a
 * re-read that finds the stamp unchanged, rather than running for the life of
 * the page.
 *
 * Returns the teardown; the caller stops watching when the card unmounts.
 */
export function watchFlowWindow(handle: Window, onEnded: (reason: OidcFlowEnd) => void): () => void {
  let done = false;
  const teardown = () => {
    done = true;
    window.clearInterval(timer);
    window.clearTimeout(deadline);
    window.removeEventListener("message", onMessage);
    channel?.close();
  };
  const finish = (reason: OidcFlowEnd) => {
    if (done) {
      return;
    }
    teardown();
    onEnded(reason);
  };
  const onMessage = (event: MessageEvent) => {
    // Only the window we opened, only from this origin, and even then the
    // message is a nudge to ask the server, never the server's answer.
    if (event.origin !== window.location.origin || event.source !== handle) {
      return;
    }
    if ((event.data as { type?: unknown } | null)?.type === OIDC_FLOW_RETURN_MESSAGE) {
      finish("returned");
    }
  };
  const onBroadcast = (event: MessageEvent) => {
    // Nothing but this application can address the channel, so there is no
    // origin to check — and nothing to check it against either, since the
    // message says only that a round trip ended.
    if ((event.data as { type?: unknown } | null)?.type === OIDC_FLOW_RETURN_MESSAGE) {
      finish("returned");
    }
  };
  const timer = window.setInterval(() => {
    if (!handle.closed) {
      return;
    }
    window.clearInterval(timer);
    if (!done) {
      onEnded("closed");
    }
  }, CLOSED_POLL_MS);
  const deadline = window.setTimeout(() => finish("expired"), FLOW_WATCH_LIMIT_MS);
  const channel = openReturnChannel();
  if (channel !== null) {
    channel.onmessage = onBroadcast;
  }
  window.addEventListener("message", onMessage);
  return teardown;
}

/**
 * Run before the application renders. When this document *is* the flow window
 * coming back, it says so and closes, so the operator never sees a second copy
 * of Settings and the card learns to re-read straight away.
 *
 * It says so twice, because the opener link is not guaranteed to survive the
 * trip: an identity provider serving `Cross-Origin-Opener-Policy: same-origin`
 * severs it on the way out and it does not come back on the way in. The
 * broadcast is the carrier that does not depend on it. Both say the same
 * nothing — that a round trip ended — and neither carries a verdict.
 *
 * Returns whether it handled the document, so the caller can skip mounting.
 * With no opener it answers `false` even though it did report back: a window
 * disowned that way is no longer script-closable in every browser, and a second
 * copy of Settings in a small window is a better worst case than a blank one
 * the operator cannot read and the application never mounted into.
 */
export function closeFlowWindowOnReturn(): boolean {
  if (typeof window === "undefined" || window.name !== OIDC_FLOW_WINDOW_NAME) {
    return false;
  }
  if (!FLOW_RETURN_PATHS.has(window.location.pathname)) {
    return false;
  }
  const channel = openReturnChannel();
  try {
    channel?.postMessage({ type: OIDC_FLOW_RETURN_MESSAGE });
  } catch {
    /* The opener message below is the other half of the same sentence. */
  } finally {
    // Closing the sender does not cancel a message already posted.
    channel?.close();
  }
  const opener = window.opener as Window | null;
  if (opener === null) {
    return false;
  }
  try {
    opener.postMessage({ type: OIDC_FLOW_RETURN_MESSAGE }, window.location.origin);
  } catch {
    /* A closed opener is the same as no opener: close anyway. */
  }
  window.close();
  return true;
}
