## Why

HTTP-bridge transcript persistence is best effort, but terminal delivery currently
waits without an application-level bound for the SQLite writer. A busy writer can
therefore delay an otherwise complete live response for up to the database busy
timeout even though an incomplete transcript is already safe to exclude from
replay.

## What Changes

- Bound the terminal transcript drain and append attempt.
- Treat a bound expiry like an append failure: deliver the selected terminal
  event, keep replay disabled, and attempt the existing owner-fenced fallback
  settlement.
- Preserve successful atomic append and replay behavior when persistence
  finishes within the bound.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: optional transcript persistence cannot indefinitely
  block a live HTTP-bridge terminal response.

## Impact

The HTTP-bridge event batcher and terminal delivery failure path are affected.
Public response shapes, database schema, and successful replay semantics are
unchanged.
