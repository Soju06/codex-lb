# Tasks

- [x] Add `input_item_count`/`input_full_fingerprint` to `RequestLog` and a
      matching Alembic migration (nullable, additive).
- [x] Persist those fields on every successful response completion in the
      direct retry path, native WebSocket, and their shared request-log
      writer.
- [x] Warm `_websocket_continuity_index` from the durable row inside
      `_resolve_websocket_previous_response_owner`, without ever overwriting
      an existing entry.
- [x] Recompute `verified_fresh_replay_payload` in `_stream_with_retry` after
      owner resolution so a cold-cache request can still recover.
- [x] Add the missing client-anchored fresh-replay recovery branch to
      `_stream_via_http_bridge`, scoped to `previous_response_owner_unavailable`
      and disjoint from the existing proxy-injected reattach branch.
- [x] Add regression coverage for: durable fallback recovering a cold-cache
      direct-path request, and durable fallback recovering a client-anchored
      HTTP-bridge request; verify each fails without the fix.
- [x] Update pre-existing tests broken by the new `request_logs` columns
      (repository unit tests, websocket observability dict-equality tests).
- [x] Run focused and full test suites, ruff check/format, and `ty check`.
