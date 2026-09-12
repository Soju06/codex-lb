## Why

On the direct Responses WebSocket surface, codex-lb injects a session-continuity
`previous_response_id` (`continuity_state.last_completed_response_id`) into a
follow-up `response.create` and trims the already-stored history prefix. When
upstream denies that proxy-injected anchor with `previous_response_not_found`
and the retained payload is not a retry-safe fresh replay (for example tool
outputs whose calls are not in the same `input` after client-side compaction),
the turn fails closed and the fail-closed path remembers the denied id in the
process-local stale previous-response memory. That memory was consulted only by
owner lookup, never by anchor injection, and the continuity anchor itself was
cleared only on a completion without a response id. The client's retry therefore
re-sent the same prefix, the same dead anchor was injected again, and the turn
failed identically until the client abandoned the thread (issue #1921,
mechanism 3 in the maintainer's 2026-09-08 breakdown; six identical failures in
eight seconds on one thread in the 1.25.0-beta.5 reproduction).

## What Changes

- The session-anchor decision (`_websocket_continuity_anchor_for_payload`)
  consults the stale previous-response memory for the request's API key before
  the stored-prefix comparison. When the candidate anchor is remembered as
  denied, the anchor is retired from session continuity (response id, stored
  input count and prefix fingerprint, pending tool-call metadata), a
  `websocket_session_anchor_retired … reason=stale_previous_response` line is
  logged, and no anchor is injected.
- The request then goes upstream exactly as the client sent it: no
  `previous_response_id`, untrimmed `input`, and the request state is not
  marked as carrying a proxy-injected anchor.
- The completion path that clears an absent response anchor reuses the same
  retirement helper (no behavior change).
- A client-supplied `previous_response_id` is untouched; the rule applies only
  to the proxy-injected session anchor.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: adds a direct-WebSocket requirement that a session
  anchor already remembered as denied is retired instead of re-injected, the
  WebSocket counterpart of the existing HTTP-bridge rule "Explicit upstream
  previous-response denials retire proxy-injected anchors".

## Impact

- `app/modules/proxy/_service/websocket/helpers.py`
  (`_retire_websocket_continuity_anchor`, `_websocket_continuity_anchor_for_payload`
  gains `api_key_id`, `_record_websocket_continuity_completion` reuses the
  helper) and `app/modules/proxy/_service/websocket/mixin.py` (passes the
  refreshed API key id, the same key the fail-closed path used to remember the
  denial).
- No new setting, dependency, migration, route, or error envelope. The stale
  memory keeps its existing 60-second TTL and 4096-entry bound; retiring the
  continuity anchor is what prevents the id from returning after expiry.
- Codex-faithful: the only wire effect is that a retry whose injected anchor is
  known-dead is sent as the client's own unanchored full-context
  `response.create` instead of a proxy-trimmed body with a rejected anchor.
- Regression coverage:
  `tests/unit/test_proxy_utils.py::test_prepare_websocket_response_create_request_retires_injected_anchor_remembered_stale`.
