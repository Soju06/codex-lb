# Fix cross-account compact previous_response_id anchor wedge

## Why

The HTTP-bridge "compact anchor" continuity optimization injects
`previous_response_id = session.last_completed_response_id` and trims the
already-stored history prefix so a follow-up turn only carries the new items.

A `previous_response_id` is **account-scoped** on the upstream Responses API:
only the account that created the response can resume it. The injection had no
account-ownership check. When a Codex session fails over to a different account
(e.g. the durable owner account became unavailable, or a durable session record
is restored onto a new session bound to another account), the anchor points at a
response the serving account never created. Upstream then accepts the WebSocket
`response.create` but **never emits `response.created`**, and because the history
was trimmed away there is no fallback. The per-bridge `response_create_gate`
(a `Semaphore(1)`) stays held: the holder's own client eventually reports
`stream disconnected before completion: idle timeout waiting for SSE`, and later
requests on the same session time out as `codex-lb is temporarily overloaded
during http_bridge_response_create_gate`.

Observed live on 2026-07-10: sol sessions that fanned across three accounts
repeatedly wedged on the same anchor (`resp_0bc0310d…`) even though the accounts
had quota. Freeing the gate (the stuck-gate retire backstop) does not stop the
recurrence because the session re-injects the same cross-account anchor.

## What Changes

- Track the account that owns `last_completed_response_id` on the bridge session
  (`last_completed_response_account_id`), set in lockstep at every setter: the
  real upstream `response.completed` path records the session's current account;
  the durable-restore path records the durable owner account.
- The session-level compact-anchor injection MUST only fire when the anchor's
  owning account equals the session's serving account. Otherwise the request
  falls through to a full-history resend (correct output, slightly more tokens),
  never a cross-account `previous_response_id`.

## Impact

- Affected specs: `sticky-session-operations`
- Affected code: `_service/support.py` (`_HTTPBridgeSession`),
  `_service/http_bridge/streaming.py` (injection guard + durable-restore owner),
  `_service/http_bridge/upstream_events.py` (completion owner).
- Behavior: on cross-account failover, continuity is preserved by resending full
  history instead of an unresolvable anchor. No client-visible protocol change.
- Follow-ups (tracked separately, not in this change): the durable direct-anchor
  injection performed before session/account binding, the WebSocket-transport
  anchor path, and a proactive `response.created` watchdog that replays the
  stored full-history payload if any anchored request stalls.
