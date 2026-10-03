# Keep the Prewarm Anchor on WebSocket Replay

## Why

Codex CLI opens each WebSocket session with an empty `generate: false` prewarm that carries the tools and the
session's developer context. Its first turn sends `previous_response_id` set to the prewarm plus only the new items.
An empty prewarm completion is deliberately not recorded as completed continuity, so when that first turn arrives the
full-resend check finds no stored context for the anchor and accepts the new items as a self-contained full resend.

If the upstream WebSocket then closes before `response.created`, or rejects the anchor, the transparent replay sends
that body without `previous_response_id`. The model receives only the new items, without the instructions, tools, or
developer context, and answers as if the session had none. Nothing tells the client that context was dropped.

## What Changes

- An empty prewarm completion records its response id and input prefix (count and fingerprint) separately from turn
  continuity, so it still does not count as turn progress or previous-response ownership.
- A request whose `previous_response_id` is that prewarm is retry-safe without the anchor only if its input repeats
  the prewarm's input as a prefix. A delta fails like any other unsafe continuation, and the client resends the turn.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: "WebSocket full-resend previous-response misses retry without stale anchor" treats a delta
  chained to an empty prewarm as not self-contained.

## Impact

- `app/modules/proxy/_service/support.py` (`_WebSocketContinuityState`).
- `app/modules/proxy/_service/websocket/helpers.py` (`_websocket_client_previous_response_full_resend_is_retry_safe`,
  `_record_websocket_empty_prewarm_completion`).
- `app/modules/proxy/_service/websocket/mixin.py` (empty prewarm completion handling).
- Empty prewarm completions still do not update account success state, previous-response ownership, or turn
  continuity ("Codex WebSocket prewarm completions are classified separately" is unchanged).
