# Return Half-Open Probes Exclusively

## Why

When a hard HTTP bridge key is already in half-open retry-circuit state, a
server-side continuity failure can consume the single probe without proving an
upstream transport defect. The key must not stay locked for the whole lease, but
returning the lease must also not admit every concurrent reconnect.

## What Changes

- Returned half-open probes become an elapsed cooldown, so the next admitted
  reconnect must acquire a fresh half-open lease.
- A returned probe is bound to the session that acquired it, so late cleanup
  from another session cannot clear a newer active lease for the same key.
- Local stale-anchor resets disarm their pending response-create attempts before
  returning the probe after detaching the session, so teardown cannot race the
  reader into recharging the circuit.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: HTTP bridge retry-circuit half-open recovery.

## Impact

- HTTP bridge retry circuit and stale-anchor reset paths.
- No API, schema, migration, dependency, dashboard, or setting changes.
