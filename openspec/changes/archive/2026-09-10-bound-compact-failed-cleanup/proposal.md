## Why

Compact double-write failure adds an indefinite detached retry per request. Active-write concurrency does not bound retained work, and the compact retry prefix is absent from the canonical persistence classifier.

## What Changes

- Keep shielded settlement and the immediate fail-safe release, including their existing persistence retries.
- After both fail, retain the durable reservation for existing stale reclamation instead of creating detached retry work.
- Preserve the settlement error, unconfirmed-release flag and health-write ordering.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: define exceptional compact cleanup failure disposition.

## Impact

Compact failure cleanup only. No new settings, thresholds, queues, schema or changes to stream retry ownership. Runtime accepted this source-only direction; live transition remains separate.
