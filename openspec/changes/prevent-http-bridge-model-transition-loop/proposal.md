## Why

When a request carries a fresh generated turn-state header plus a session header whose live bridge uses another model, the HTTP bridge lookup can repeatedly discard its internal model fork and reapply the session-header fallback. The request spins before account selection, emits an unbounded `model_transition_fork` log stream, and other clients stop making progress until the process restarts.

## What Changes

- Preserve an internal parallel bridge key and its session-header fallback state once lookup has selected it instead of resolving the original turn-state/session headers again on the next creation-loop iteration.
- Protect the incompatible session-header parent from capacity eviction while the isolated child is created.
- Cover follow-up requests without a previous-response/durable lookup and full-cache model transitions, including parent sessions that finish creation while the child waits.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `responses-api-compat`: model-transition isolation MUST terminate after one fork and continue bridge creation without reusing the incompatible session.

## Impact

`app/modules/proxy/_service/http_bridge/mixin.py` and focused HTTP bridge unit coverage. No API, schema, configuration, or archive-format change.
