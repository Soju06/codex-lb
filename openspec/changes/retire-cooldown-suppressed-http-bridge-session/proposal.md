# Retire Cooldown-Suppressed HTTP Bridge Sessions

## Why

Issue #1943 exposes a lifecycle leak in the HTTP Responses bridge. A request
can create and register an upstream WebSocket before the late retry-circuit
admission check runs. When hard-key cooldown suppresses that request, the proxy
returns the expected 503 but leaves the newly created session reusable. The
orphaned socket can then be selected by a later half-open probe and keep the
key in a repeated failure loop.

The same leak exists in the startup pre-submit cooldown path used for
continuity-bound requests: it returns a synthetic 503 before submit without
retiring the session that was already opened.

## What Changes

- Mark a session as `reconnect_requested` and `retire_after_drain` whenever a
  hard-key cooldown rejects a request before upstream dispatch.
- Invoke the existing bounded drain-retirement helper immediately so an idle
  session is detached and closed; sessions with other pending work remain
  non-reusable until that work drains.
- Cover both late submit suppression and startup pre-submit terminal paths.
- Preserve proof-gated and operation-fenced replay bypasses, durable retry
  circuit state, reservation settlement, and the existing 503 envelope.

## Impact

- Cooldown-suppressed bridge sessions cannot be reused for later requests.
- Newly opened but never-dispatched WebSockets are closed and removed from the
  registry when no other work owns the session.
- Shared sessions with pending work drain through the existing lifecycle and
  are not force-closed or double-settled.
- No new setting, schema, migration, retry threshold, or cooldown duration is
  introduced.

## Source

- GitHub issue: Soju06/codex-lb#1943
- Feasibility analysis: Fable5 response `msg_l4vLQ0i8RzQiFjCGE2W7cOiE`
- Source sidechat: 01a04718-7322-72d0-b4b3-8f5bb4581157
