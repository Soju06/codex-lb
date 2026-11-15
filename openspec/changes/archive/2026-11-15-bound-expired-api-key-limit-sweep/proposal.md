## Why

The hourly API-key expired-limit sweep can repeatedly select a legacy SQLite row whose second-precision timestamp does not match SQLAlchemy's bound timestamp in the guarded update. This busy loop prevents the sweep from reaching later expired limits (upstream issue #2652 and PR #2654).

## What Changes

- Visit each selected expired limit at most once per invocation using increasing primary-key batches, including guarded update misses.
- Preserve exact timestamp guards, bounded batches, per-batch commits, reset-window advancement and successful-reset counting.
- Add unit and real SQLite regressions with legacy timestamps and later resettable rows.

## Capabilities

### Modified Capabilities

- `api-keys`: Require forward progress of the background expired-limit reset sweep.

## Impact

Only the API-key repository sweep, regression tests and owning specification change. Legacy timestamp normalization and lazy-reset/reservation precision compatibility remain outside this fix; guarded misses remain unchanged for separate repair. No migration, new setting or dependency is required.
