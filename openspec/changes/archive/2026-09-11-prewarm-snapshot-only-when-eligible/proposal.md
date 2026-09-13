## Why

`no-settings-reads-under-prewarm-lock` (#2335) moved the Codex HTTP-bridge
prewarm's dashboard-settings read out of `prewarm_lock` and threaded the
snapshot into the admission gate and the reconnect. It resolves that snapshot
unconditionally, before the warm-up payload has been built.

Most prewarm candidates never send a warm-up: `_build_http_bridge_prewarm_text`
returns `None` for a payload that already carries `generate: false`, is not a
JSON object, or is otherwise not warm-up shaped. Those requests reach neither
of the two readers the snapshot exists for, so before #2335 they took no
settings read at all and now they take one — pure cost on a cached hit, a
database query on an expired one, and a needless failure surface (handled, but
still exercised) on a request that is served normally either way.

## What Changes

- Build the warm-up text before taking `prewarm_lock` and resolve the settings
  snapshot only when there is a warm-up to send, so an ineligible payload reads
  nothing, as it did before #2335.
- Keep the locked body's decisions identical: it still re-checks
  `session.prewarmed` under the lock, still sets it, and still records
  `prewarm_status=skipped` for a payload with no warm-up. The builder is a pure
  function of the request text, so the answer cannot differ.
- As a side effect the payload parse leaves the critical section, which is
  aligned with why the lock is being kept short in the first place.
- Assert the snapshot's identity, not just its presence, where the prewarm
  timeout path hands it to the admission gate and the reconnect.

No dashboard value changes meaning, and no value is read that was not read
before; only the condition under which it is read changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: The prewarm snapshot must be resolved only when a
  warm-up will actually be sent.

## Impact

- Affected code: the HTTP-bridge prewarm helper.
- Affected tests: HTTP-bridge prewarm unit coverage and the prewarm-timeout
  observation assertions.
- No schema, API, or configuration surface change.
- Ordering: this change's delta is written against the requirement text
  `no-settings-reads-under-prewarm-lock` introduces (already merged, still
  unarchived on main), so its text is a superset of that change's. Archiving
  the two in either order converges on the same spec, verified by simulation.
