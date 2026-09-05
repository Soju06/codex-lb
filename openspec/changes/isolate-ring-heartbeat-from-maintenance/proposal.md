## Why

A live replica can stop refreshing its bridge-ring row when durable-ownership reconciliation, idle-session sweeping, or cap-partition refresh stalls in the same serial loop. The replica then ages out of the ring while still serving, and a single-replica `/health/ready` response can remain successful with `ring_size=0` and `is_member=false`, hiding the failure from routing and operators.

## What Changes

- Give ring renewal an independently supervised periodic owner whose cadence is not delayed by optional bridge or cap-partition maintenance.
- Run durable-ownership reconciliation, idle-session sweeping, and cap-partition refresh as separately supervised, bounded phases; a slow, failed, or cancelled phase cannot skip ring renewal or another maintenance phase, and a timed-out owner is not duplicated while it remains unfinished.
- Preserve explicit lifecycle ownership: all periodic owners are strongly referenced, cancelled and drained during shutdown, and ring stale-marking happens only after the renewal owner stops.
- Make bridge-enabled readiness fail after registration whenever the probed replica is absent from the active ring, including an empty ring, while keeping liveness independent of ring and upstream state.
- Expose the probed replica's heartbeat age plus low-cardinality heartbeat success/failure and maintenance outcome diagnostics.
- Add deterministic regressions for blocked maintenance, unexpected worker exit, phase timeout/no-overlap, shutdown cleanup, stale/empty-ring readiness, and heartbeat-age observability.
- No database migration, new setting, request-wire change, or dashboard surface change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `bridge-ring-membership`: Ring renewal becomes independently supervised, bounded, and readiness-authoritative for the local replica.
- `sticky-session-operations`: Durable ownership reconciliation and idle-session sweeping run on independent periodic maintenance owners rather than in the renewal critical path.
- `proxy-admission-control`: Cap-partition membership refresh remains periodic but no longer runs serially behind ring renewal or bridge maintenance.
- `proxy-runtime-observability`: Health payloads, metrics, and structured logs expose local heartbeat age and periodic phase outcomes without high-cardinality labels.

## Impact

Affected runtime code includes lifespan task ownership in `app/main.py`, ring membership/health reporting, Prometheus metric declarations, cap-partition and HTTP-bridge maintenance wiring, and shutdown cleanup. Tests will cover health probes, lifecycle supervision, ring membership, cap partitioning, bridge upkeep, and partial failure. Existing database tables, public proxy APIs, settings, heartbeat/stale intervals, routing algorithms, and maintenance selection semantics remain unchanged.
