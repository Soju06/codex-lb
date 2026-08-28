## Why

Codex turn metadata is supplied on the downstream WebSocket handshake, but codex-lb currently persists its `request_kind` value on every turn sent over that connection. A socket opened by a `prewarm` handshake therefore causes later foreground turns to be reported as prewarms even when they generate normal model output.

## What Changes

- Classify each Responses WebSocket turn from the handshake intent and that turn's `response.create` payload, so only a prewarm handshake carrying `generate: false` is persisted as `request_kind=prewarm`.
- Persist the parsed handshake value separately as nullable `connection_request_kind` request-log metadata and expose it through the request-logs API.
- Keep native-WebSocket prewarm settlement behavior tied to the corrected per-turn classification.
- Add regression coverage for a long-lived connection opened by a prewarm handshake that subsequently carries an ordinary generated turn.
- Leave historical request rows unchanged because folded usage rollups cannot be safely rewritten and failed rows do not retain enough payload data for deterministic reclassification.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Define per-turn request-kind attribution separately from connection-handshake metadata for direct Responses WebSockets.

## Impact

- WebSocket request preparation and terminal request logging under `app/modules/proxy/_service/`.
- `request_logs` persistence model, additive Alembic migration, repository, and dashboard API schema.
- Responses WebSocket and request-log API regression tests.
- No direct change to Codex CLI behavior, current-request account routing, API-key traffic-class admission, quota enforcement, per-request budgeting, or reservation settlement.
- Correcting the persisted dimension restores generated WebSocket turns to quota-planner demand forecasts, which consume `normal`/`real` rows for simulation and reserve/warmup scheduling when enabled.
