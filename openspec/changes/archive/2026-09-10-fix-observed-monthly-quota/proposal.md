## Why

Issue #1367 reports Team monthly quota hidden or shown as 5h. Current main reproduces the hidden quota through GET /api/accounts because presentation depends on an estimated credit capacity.

## What Changes

- Normalize lone observed 28–32 day quota windows as monthly in poll and live ingestion, including zero-duration secondary placeholders.
- Preserve monthly percentages for plans without monthly credit estimates while retaining protection against stale monthly samples after a plan upgrade.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-quota-presentation`: monthly observation applies independently of account plan and credit estimate.

## Impact

Backend usage normalization and account summaries. No new configuration, dependencies, schema migration, or frontend implementation.
