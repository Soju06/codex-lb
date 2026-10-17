## Why

A detached stream settlement can fail, enqueue its reservation-release fallback,
and then lose the reservation when that fallback also hits a transient
persistence failure. The task drain reports success even though the reservation
still consumes quota until stale recovery runs hours later.

## What Changes

- Keep a failed detached reservation release tracked and retry it after
  transient persistence failures, with a shared concurrency bound on repository
  attempts.
- Make the persistence drain report completion only after the tracked
  settlement/release chain has actually terminated.
- Add deterministic regression coverage for a finalize failure followed by one
  failed release attempt, while preserving successful settlement, cancellation,
  and SQLite-lock behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Clarify that a detached settlement fallback which itself fails
  transiently remains tracked and retries before persistence drain can succeed.

## Impact

The change is limited to detached API-key reservation cleanup in the proxy
service, its focused persistence tests, and the existing API-key settlement
contract. It adds no API, setting, dependency, migration, or dashboard change.
