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
  settings read under that lock" to "MUST NOT read settings while that lock is
  held", with a scenario covering a full prewarm body.
- Assert the invariant directly: a prewarm that builds its warm-up, takes
  admission and sends upstream records exactly one settings read, before the
  lock, and opens no database session.

No dashboard value changes meaning; only where it is read changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: The Codex prewarm switch requirement now forbids
  any settings read while the prewarm lock is held, not just newly added ones.

## Impact

- Affected code: HTTP-bridge prewarm/reconnect helpers and the proxy
  response-create admission gate.
- Affected tests: HTTP-bridge prewarm and reconnect unit coverage.
- No schema, API, or configuration surface change.
