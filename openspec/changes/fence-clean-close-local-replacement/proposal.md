## Why

An HTTP Responses bridge can receive `response.completed` and then observe a
clean upstream WebSocket close while the next request for the same soft
affinity key is starting. The retiring session is removed from the local
registry before its durable release completes. A replacement created in that
window can reuse the same durable owner epoch, allowing the retiring session's
late fenced release to clear the replacement's ownership and produce an
intermittent `bridge_instance_mismatch` response on a single instance.

## What Changes

- Treat a durable row owned by the current instance without a reusable local
  session as a local replacement boundary.
- Advance the durable owner epoch before publishing that fresh local session,
  so cleanup from the detached generation is fenced out.
- Add deterministic integration and repository-level regression coverage for
  clean-close replacement overlapping a late durable release.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `responses-api-compat`

## Impact

- **Code:** HTTP bridge session creation and durable-ownership claim behavior.
- **API:** no schema changes; the second request remains successful instead of
  intermittently returning HTTP 409 on a single-instance clean-close rollover.
- **Persistence:** replacement claims advance the existing durable owner epoch;
  no migration is required.
