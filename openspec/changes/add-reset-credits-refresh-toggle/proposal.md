## Why

Reset-credit polling runs in every replica and issues one authenticated upstream `GET /wham/rate-limit-reset-credits` per eligible account per interval. Operators who do not use the reset-credit dashboard surface (or who run many replicas against large account fleets) currently have no way to shed that upstream call volume: the spec mandated that the scheduler always starts, and `rate_limit_reset_credits_refresh_interval_seconds` is constrained to positive values, so "off" is not expressible — stretching the interval still keeps periodic authenticated upstream traffic and the associated log/failure noise.

## What Changes

- Add setting `rate_limit_reset_credits_refresh_enabled` (default `true`) that gates background reset-credit polling.
- When disabled, the scheduler's `start()` is a no-op: no background task is created, no upstream fetches occur, and snapshot caches simply stay empty (dashboard reads already handle a missing snapshot as `null`/`0`).
- Default `true` preserves current zero-config behavior; nothing changes for existing deployments.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `rate-limit-reset-credits`: The scheduler starts with the application lifespan only when reset-credit polling is enabled, and the settings surface gains an enable/disable toggle alongside the existing interval control.
