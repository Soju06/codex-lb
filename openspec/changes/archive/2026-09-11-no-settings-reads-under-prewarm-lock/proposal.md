## Why

The Codex HTTP-bridge prewarm holds a per-session `prewarm_lock` across its
whole body. Two helpers it calls under that lock read the dashboard settings
row for themselves: the response-create admission gate (account concurrency
caps and routing tunables) and the reconnect the prewarm-timeout recovery
takes. A settings-cache read refreshes behind a process-global lock and runs a
database query, so one stalled refresh suspends the critical section and
stalls every later turn on that session -- the pattern that wedged every keyed
submit in issues #1971 and #1972.

`dashboard-managed-codex-prewarm` could only require that the bridge add no
new settings read under that lock, because those two reads already existed.
With both of them threaded from a snapshot taken before the lock, the
invariant can be the true one: no settings read at all while the lock is held.

## What Changes

- Resolve the dashboard settings row once in the prewarm path, before taking
  `prewarm_lock`, and pass it into the admission gate and the reconnect.
- Give both helpers an optional snapshot parameter that defaults to today's
  behaviour (read the cache), so every other caller is unaffected.
- Upgrade the deployment-installation prewarm wording from "MUST NOT add a
  settings read under that lock" to "the prewarm's own helpers MUST NOT read
  the dashboard row for themselves while that lock is held", naming the
  residual work the timeout recovery still reaches (account selection, token
  refresh, upstream route resolution) as out of scope.
- Apply the request entry point's snapshot-failure fallback to that pre-lock
  read (last loaded row, else skip the prewarm) so moving the read cannot fail
  a request the bridge can otherwise serve.
- Assert the invariant directly: a prewarm that builds its warm-up, takes
  admission, sends upstream and completes records exactly one settings read,
  attributed to the pre-lock snapshot, and opens no database session.

No dashboard value changes meaning; only where it is read changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: The Codex prewarm switch requirement now forbids
  any settings read while the prewarm lock is held, not just newly added ones.

## Out of scope

The prewarm timeout path reconnects while holding the lock, and the reconnect
reaches account selection, token refresh and upstream route resolution, each
of which has its own settings read or database session. Threading the snapshot
through those would be a much larger change (route resolution has its own
cache), so this change removes the two reads the prewarm's own helpers take
and states the invariant at exactly that scope.

## Impact

- Affected code: HTTP-bridge prewarm/reconnect helpers and the proxy
  response-create admission gate.
- Affected tests: HTTP-bridge prewarm and reconnect unit coverage.
- No schema, API, or configuration surface change.
