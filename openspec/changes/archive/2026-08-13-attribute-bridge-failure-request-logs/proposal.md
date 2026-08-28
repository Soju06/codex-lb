## Why

Error request-log rows written by the HTTP-bridge failure fan-out
(`_fail_pending_websocket_requests`) store `api_key_id = NULL` even though the
request was authenticated. The bridge session is shared across API keys, so
its failure callers pass a session-level `api_key=None`, and the fan-out uses
that instead of each pending request's own key. In production every
`transport=http / upstream_transport=websocket` error in this family
(`stream_incomplete`, `upstream_request_timeout`, `upstream_unavailable`,
`upstream_rejected_input`, ...) is unattributed — 522 rows over 7 days — so
dashboard and API queries filtered by key silently miss them. This violates
the existing `api-keys` requirement "RequestLog API key reference"
(authenticated requests SHALL record `api_key_id`).

## What Changes

- The bridge failure fan-out attributes each pending request's log row to
  that request's own authenticated key (`request_state.api_key`), falling
  back to the caller-provided key only when the request state carries none.
- No behavior change for direct WebSocket callers, which already pass the
  connection-level key: per-request attribution takes precedence and is
  identical there.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: clarify that the RequestLog API key reference requirement also
  holds for error rows written by shared-session (bridge) failure fan-out.

## Impact

- Code: `app/modules/proxy/_service/websocket/mixin.py`
- Tests: `tests/unit/test_websocket_upstream_transport_observability.py`,
  `tests/integration/test_http_responses_bridge.py` (route-level regression at
  the externally failing surface: authenticated `/v1/responses` bridge send
  failure → persisted `RequestLog.api_key_id`)
- Specs: `openspec/specs/api-keys/spec.md`
