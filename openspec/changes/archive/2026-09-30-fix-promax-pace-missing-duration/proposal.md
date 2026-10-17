## Why

A fresh Pro Max secondary usage observation remains unquantified when its
optional window duration is absent or zero. The weekly-pace builder currently
requires a seven-day duration before suppressing the forecast, so it can
return a known-account-only forecast as though fleet coverage were complete.

## What Changes

- Suppress fleet weekly pace for fresh Pro Max secondary observations without
  relying on their optional duration metadata.
- Cover both dashboard overview and projections, alongside the existing
  missing-reset regression.
- Preserve the account-status and freshness gates and existing known-plan
  forecast calculations.

## Capabilities

### Modified Capabilities

- `usage-refresh-policy`: incomplete secondary observations suppress the
  dashboard API's fleet weekly pace.

## Impact

The change is limited to the weekly-pace eligibility guard and its tests.
It adds no settings, dependencies, schema changes or absolute credit estimates.
