## Context

Responses upstream WebSockets use two existing transports: `websockets` for direct egress and aiohttp for routed egress. Both libraries already support ping/pong liveness detection, and the same connection policy already enables it for Realtime live sideband traffic. The Responses policy disables both mechanisms because application-frame silence is valid during long turns. That distinction is unnecessary for ping/pong: control-frame replies prove transport liveness without requiring an application event.

After a VPN disconnect, an established TCP connection can be black-holed without an immediate DNS, route, or socket exception. Downstream keepalives then keep the client attached while the upstream request remains pending. Once a request frame has been sent, however, the proxy cannot know whether upstream accepted it before connectivity was lost, so transparent replay could duplicate work or side effects.

## Goals / Non-Goals

**Goals:**

- Bound silent upstream Responses WebSocket failure detection on both direct and routed egress.
- Preserve a stable classification from transport adapter through the direct WebSocket and HTTP bridge owners.
- Settle pending work once, without replaying an ambiguously delivered request or penalizing a healthy account.
- Ensure subsequent client retries open a fresh upstream connection and therefore use the current host route.

**Non-Goals:**

- Add a host route watcher, VPN-specific integration, background recovery coordinator, or proactive socket migration.
- Change the long Responses request budget or application-event idle timeout.
- Automatically resume a turn whose upstream acceptance is unknown.
- Add a configuration setting, dependency, persistence change, or operator-facing UI.

## Decisions

### Reuse transport ping/pong support and the existing timeout

Enable `heartbeat` for routed aiohttp Responses sockets and `ping_timeout` for direct `websockets` Responses sockets. Both values come from `proxy_downstream_websocket_idle_timeout_seconds`, matching the existing live-sideband policy and keeping the fix zero-config.

Application-level watchdogs were rejected because valid Responses turns may be silent for minutes and downstream synthetic keepalives are not evidence of upstream health. A host-network watcher was rejected because it is platform-specific, cannot reliably enumerate every network transition, and duplicates transport-layer failure detection.

### Give liveness expiry a narrow stable classification

Map only library-specific ping/pong timeout signals to `upstream_websocket_liveness_timeout`: the `websockets` locally sent close with reason `keepalive ping timeout`, and aiohttp's `ServerTimeoutError` produced by its heartbeat watchdog. Ordinary upstream closes and other receive exceptions keep their current behavior.

A shared code predicate identifies account-neutral WebSocket failures so relay owners do not duplicate string comparisons as new neutral conditions are added.

### Fail closed after ambiguous delivery

Both relay owners treat the classified liveness timeout like a post-send network failure: no transparent replay, no account-health write, exact-once pending-request settlement, and retirement of the affected socket. The downstream error remains retryable at the client boundary, where a fresh client connection can safely establish a new upstream route under the client's existing retry semantics.

The HTTP bridge cannot infer settlement ownership from `session.closed`: that
flag also rejects admission after continuity-persistence and other failures
that settle only the submitting request. A submitter claims whole-deque
liveness settlement explicitly while holding the session lifecycle lock around
the failing send. The reader skips its normal settlement only when that claim
exists; a later liveness expiry on an otherwise closed session still settles
every pending sibling.

Once published, the send claim owns the whole pending deque. The submitter
therefore starts settlement as a shielded child before its next await and
defers caller cancellation until that child completes; cancellation cannot
leave a permanent claim whose reader has already yielded.

For example, if `response.create` was written before a VPN route disappears and no pong returns, codex-lb emits a terminal `upstream_websocket_liveness_timeout` failure for that pending request and closes or retires the upstream session. It does not resend `response.create` on another account or socket.

## Risks / Trade-offs

- [Some intermediaries do not answer WebSocket pings correctly] → Use the established, configurable timeout already deployed for live sideband traffic; operators can adjust the existing value if needed.
- [Detection is not instantaneous] → A bounded delay is preferable to false positives and is far shorter than the multi-hour Responses request budget.
- [The client must retry the interrupted turn] → This avoids duplicate model work and tool side effects when delivery status is unknowable.
- [Library wording could change] → Pin direct detection to the concrete close code/reason emitted by the installed `websockets` API and drive a real no-pong expiry in integration coverage, in addition to adapter unit tests.

## Migration Plan

No data or configuration migration is required. Deploy normally; rollback restores the previous policy flags and classification behavior.

## Open Questions

None.
