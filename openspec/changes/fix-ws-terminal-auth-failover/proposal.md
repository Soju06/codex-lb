# Fix WebSocket terminal auth failover

## Why

Codex-native WebSocket sessions can receive an upstream terminal `response.failed`/`error` with `invalid_api_key` and messages such as `Your session has ended. Please log in again.` after the upstream WebSocket is already open. The existing failover path handles WebSocket handshake `401` responses, but terminal WebSocket auth failures are not classified as account-local auth failures, so the selected account can remain active and subsequent requests can repeat the same failure.

## What Changes

- Treat pre-visible WebSocket terminal authentication failures as account-local auth failures.
- When the error explicitly indicates the ChatGPT session ended, mark the selected account re-authentication-required before replaying the request on another eligible account.
- For generic pre-visible WebSocket `invalid_api_key`/`authentication_error`, retry the same account once after a forced token refresh, then exclude/deactivate the account if the refreshed replay still fails with auth.
- Preserve no-replay behavior after downstream-visible output or when the request is not safely replayable.
- Add regression coverage for WebSocket terminal session-ended failover and refreshed auth-failure handling.

## Impact

- **Code**: `app/modules/proxy/service.py`, `app/core/balancer/logic.py`
- **Tests**: `tests/integration/test_proxy_websocket_responses.py`
- **Behavior**: Account-local WebSocket auth failures stop repeating on the same account when another eligible account can serve the pre-visible request.
