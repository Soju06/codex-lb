## Why

Responses-capable model sources currently accept HTTP/SSE only. WebSocket requests are rejected before source dispatch, so clients cannot keep a native source connection across tool turns even when their provider supports Responses WebSocket.

## What Changes

- Add opt-in native Responses WebSocket forwarding from client through codex-lb to a compatible model source on both public Responses paths.
- Add `supports_responses_websocket`, default false, to source persistence, management API and existing dashboard form; require Responses support and a streaming-enabled model.
- Reuse source routing, aliases, ownership publication and per-turn accounting across HTTP and WebSocket; retain source-specific credentials and permission checks.
- Introduce a bounded source session lifecycle with sequential turns, safe pre-send failover, cancellation, reconnect errors and HA draining.
- Replace the unconditional source-WebSocket prohibition with capability-based routing and fail-closed lookup before quota reservation.
- **BREAKING failure-path change**: a source lookup outage on WebSocket returns an explicit error instead of falling through to subscription selection; normal subscription and HTTP source behavior is preserved.

The native runtime, capability management, dashboard and discovery changes are implemented. Local verification is recorded in [verification.md](verification.md). The change remains active for the intended provider/client conformance checks and release verification; no production source has been enabled.

## Capabilities

### New Capabilities

- `model-source-websocket`: Native source transport, source session lifecycle, ownership, accounting, capability discovery and verification contract.

### Modified Capabilities

- `responses-api-compat`: Replace the unconditional HTTP-only source guard with the source WebSocket routing contract while preserving subscription/file/security precedence and compatibility for sources without the capability.

## Impact

- Backend: source model/schema/service/repository/catalog, source selection and payload preparation currently in `proxy/api.py`, source dispatch/ownership/pool, public WebSocket routes and session orchestration.
- Persistence: one additive default-false capability migration on the current Alembic head. No new environment variable or runtime bootstrap step.
- Tests: real public-route WebSocket fixtures, HTTP regressions, permission/continuity/failure/accounting tests, migration and dashboard coverage.
- Documentation: change-level design/context now; publish linked user documentation and sync main specifications after implementation verification.

## Non-goals

WebSocket-to-HTTP/SSE bridging, HTTP-to-WebSocket source forwarding, Chat Completions or Realtime/audio WebSocket translation, subscription overflow over source WebSocket, multiplexed `stream_id` lanes, mid-turn steering, and migration of a live socket between replicas are outside the first version.
