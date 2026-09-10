# Close recovery review gaps

## Why

Current-head review identified inconsistent nested metadata canonicalization,
potential stale retry classification for proxy-injected anchors, and nondeterministic
response lookup on timestamp ties.

## What Changes

- Canonicalize decoded turn metadata even without an installation ID.
- Verify proxy-injected stale-anchor eligibility before owner replay can consume the fresh body.
- Use the same deterministic response lookup ordering on both transcript paths.
- Exercise both pre-fence flush schedules and retain stale-event rejection assertions.

## Impact

HTTP bridge identity and websocket recovery become consistent across equivalent
inputs and scheduling order. No schema changes or new settings are introduced.
