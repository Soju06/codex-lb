# Tasks

## 1. Implementation

- [x] 1.1 Record the response id and input prefix of an empty prewarm completion on the WebSocket continuity state,
  without updating turn continuity.
- [x] 1.2 `_websocket_client_previous_response_full_resend_is_retry_safe` accepts a request chained to that prewarm only
  when its input repeats the prewarm's input as a prefix.

## 2. Verification

- [x] 2.1 Unit test `test_websocket_client_previous_response_full_resend_retry_rejects_delta_chained_to_empty_prewarm`
  (delta rejected, prefixed full resend accepted).
- [x] 2.2 Integration test `test_responses_websocket_close_replay_keeps_context_of_turn_chained_to_empty_prewarm`
  (`/v1/responses` and `/backend-api/codex/responses`): before the fix the replay after an upstream close sends only the
  delta without `previous_response_id`; after it the turn fails with `stream_incomplete` and nothing is replayed.
- [x] 2.3 ruff, pytest, strict OpenSpec validation.
