## Why

An expired SQLite limit whose `reset_at` is stored without fractional seconds can fail the reset compare-and-set indefinitely. The hourly sweep then repeatedly selects that row, consuming CPU and blocking subsequent maintenance (#2652).

## What Changes

- Visit each expired limit at most once per sweep using primary-key pagination, even when its guarded update matches nothing.
- Preserve bounded batches, per-batch commits, timestamp ownership guards, and successful-reset counting.
- Add regression coverage with legacy SQLite timestamps and later resettable rows.

## Capabilities

### Modified Capabilities

- `api-keys`: Require forward progress of the background expired-limit sweep after guarded update misses.

## Impact

Only the API-key repository sweep, regression tests, and its specification change. No migration, timestamp normalization, lazy-reset change, new setting, or dependency is included.
