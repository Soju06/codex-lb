## Why

Daily API-key limits reset at 00:00 UTC, which is 07:00 in Ho Chi Minh City. The operator needs daily windows to follow the local calendar at UTC+7.

## What Changes

- Calculate daily reset boundaries at 00:00 Asia/Ho_Chi_Minh (17:00 UTC).
- Run the existing daily alignment pass at 23:50 Asia/Ho_Chi_Minh (16:50 UTC), preserving usage counters.
- Keep persisted timestamps in UTC and preserve the hourly fallback, lazy expiry, leader gating, and other limit windows.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Use the Ho Chi Minh City calendar for daily reset and alignment schedules.

## Impact

Daily boundary calculation, scheduler timing and logs, API-key regression tests, and the API-key spec/context. No new configuration, dependencies, or database migration.
