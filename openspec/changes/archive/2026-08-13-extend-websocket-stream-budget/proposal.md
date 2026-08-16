# Extend WebSocket Stream Budget

## Why

Native Codex WebSocket Responses turns still used the generic
`proxy_request_budget_seconds` watchdog even though HTTP Responses streams
already have a dedicated `http_responses_stream_request_budget_seconds` budget.
On the live stack this left WebSocket turns capped at 600 seconds while the
Responses stream budget and stream idle timeout were 7200 seconds. Long Codex
reasoning turns could therefore be ended by the local proxy budget while the
downstream client was still alive and receiving keepalives.

## What Changes

- `_stream_request_budget_seconds` now applies
  `http_responses_stream_request_budget_seconds` to both HTTP and WebSocket
  Responses streams.
- Native WebSocket initial connection and reconnect account selection use that
  same stream-specific deadline instead of expiring at the generic request
  budget.
- The existing fallback to `proxy_request_budget_seconds` is preserved for old
  settings objects that do not define the stream-specific budget.
- Unit coverage updates the previous HTTP-only budget expectation and keeps
  fallback coverage.

## Impact

- Affected spec: `responses-api-compat`
- Affected code: `app/modules/proxy/_service/streaming/helpers.py`,
  `app/modules/proxy/_service/websocket/mixin.py`
- Affected tests: `tests/unit/test_proxy_utils.py`
