## Why

Limit warm-up currently runs only after a reset-confirmed exhausted window recovers. That misses the idle-account case where operators want accounts to open their primary windows before user traffic arrives. The feature needs an explicit opt-in contract because it can generate synthetic upstream traffic.

## What Changes

- Add disabled-by-default staggered idle warm-up settings for the limit warm-up scheduler.
- Allow opted-in accounts with a fully unused primary 5h window to receive at most one minimal warm-up request per reset window.
- Space idle warm-up attempts across the primary reset window using deterministic per-account staggering.
- Surface the global idle warm-up toggle in settings and account cards.
- Add database persistence for the idle warm-up setting and attempt deduplication metadata.

## Impact

- Backend limit warm-up scheduler and service logic.
- Settings API/repository/service/schema flow.
- Dashboard settings and account-card rendering.
- Alembic migration and migration tests.
