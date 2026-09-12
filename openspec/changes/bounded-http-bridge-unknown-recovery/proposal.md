## Why

An upstream Responses request can become `UNKNOWN` after the session's latest
response anchor has advanced. The existing exact-fingerprint and latest-parent
fences then fail closed even when a reconnect is the same logical turn.

## What Changes

- Add an opt-in, bounded lookup for recent `UNKNOWN` operations in the same
  durable session and model.
- Match the reconnect against the stored canonical request body while ignoring
  only bridge-owned operation and account-installation metadata.
- Recover only one candidate with a non-empty prior response parent, a creation
  age within the bounded window, and no ambiguity; otherwise retain the
  existing fail-closed behavior.
- Add a session/state/creation-time index so the recovery lookup does not scan
  unrelated operation rows.

The behavior is gated by the existing parked-recovery setting and remains
disabled by default. Public request and response shapes are unchanged.

## Impact

The durable bridge repository, HTTP bridge submission path, operation indexes,
Alembic migrations, and continuity regression tests are affected. This remains
an at-least-once recovery aid: upstream idempotency or status lookup is still
required for a mathematically definitive delivery result.
