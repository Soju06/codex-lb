## Why

The dashboard request-log detail view currently exposes only the proxy-wide `latency_ms` timing, which starts earlier than the actual upstream submission point. Operators cannot see how long the upstream request itself took or compare that duration against the broader proxy latency when debugging slow requests.

## What Changes

- Persist a nullable `elapsed_ms` value on `request_logs` that measures time from immediately before upstream submission until upstream completion.
- Capture `elapsed_ms` for unary, streaming/SSE, WebSocket, and warmup request-log rows without changing the existing meaning of `latency_ms`.
- Expose `elapsedMs` from `GET /api/request-logs` alongside the existing latency fields.
- Show separate `Upstream elapsed` and `Total elapsed` fields in the Request Details dialog, where `elapsedMs` feeds the upstream duration and `latencyMs` feeds the broader proxy duration.
- Use one shared duration formatter for both values: `x.x ms` at `<= 1000` and `x.x s` above `1000`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: request-log persistence must record upstream elapsed timing separately from the existing proxy latency timing.
- `frontend-architecture`: dashboard request-log payloads and Request Details rendering must expose and display upstream elapsed timing separately from the existing total latency timing.

## Impact

- Affects `request_logs` schema and Alembic history with a new nullable `elapsed_ms` column.
- Affects proxy request-log persistence in unary, streaming, and WebSocket completion paths.
- Affects dashboard request-log schemas, formatting helpers, and the Request Details dialog UI.
- Requires backend and frontend regression coverage for timing capture, nullable legacy rows, and duration formatting.
