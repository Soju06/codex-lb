## Why

codex-lb currently assumes account quota always fits a 5h `primary` window plus a 7d `secondary` window. Free accounts no longer follow that shape: upstream now returns a single 30d limit in `primary_window`, so the current remap logic misclassifies free-account monthly quota as weekly and causes incorrect bars, trends, donut totals, API-key assigned-account badges, and stored usage semantics.

This change aligns codex-lb with the upstream free-account quota model before more monthly-only accounts accumulate misleading history and inconsistent UI state.

## What Changes

- Normalize free-account usage payloads and rate-limit payloads so `primary_window.limit_window_seconds == 2592000` with no secondary window is treated as a distinct monthly quota window instead of a disguised 5h or 7d slot.
- Update account, dashboard, overview, trend, donut, and API-key assigned-account surfaces to render free accounts as monthly-only while preserving paid-account 5h/7d behavior.
- Change free-account quota capacity semantics so only the 30d window carries quota capacity, with no synthetic 7d quota.
- Remove the current special treatment that infers weekly semantics from `primary.limit_window_seconds == 604800`.
- Add a migration that rewrites legacy free-account `usage_history.window` rows from `primary` / `secondary` to `old-primary` / `old-secondary` so old history does not conflict with the new monthly normalization.

## Capabilities

### New Capabilities
- `account-quota-presentation`: defines how monthly-only free-account quota is exposed in account, dashboard, trend, donut, and assigned-account UI surfaces.

### Modified Capabilities
- `usage-refresh-policy`: free-account usage refresh and rate-limit normalization now recognizes a distinct 30d monthly window instead of remapping it into 5h/7d semantics.
- `database-migrations`: free-account legacy usage-history rows are remapped so new monthly semantics do not collide with historical primary/secondary labels.
- `api-keys`: assigned-account selection and edit/create dialog quota badges must reflect monthly-only free-account quota instead of weekly badges.

## Impact

- Affected backend modules include usage refresh, quota normalization, proxy rate-limit payloads, account mappers, dashboard aggregation, and any logic that currently assumes only `primary` / `secondary` windows.
- Affected frontend modules include account cards/lists/detail panels, account trends, dashboard donuts and progress bars, overview usage displays, and API-key assigned-account selectors.
- The `/wham/usage` free-account payload remains the upstream source of truth; codex-lb changes only how it normalizes, stores, and presents that data.
- An Alembic migration is required to isolate historical free-account rows before normalized monthly writes begin.
