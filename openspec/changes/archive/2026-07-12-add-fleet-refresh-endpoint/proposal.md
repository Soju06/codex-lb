## Why

External local dashboards need a truthful way to distinguish a fresh read of codex-lb state from an upstream usage refresh attempt. The current live fleet view can read a summary, but upstream usage refresh remains background-only, so a manual refresh button in a sibling dashboard cannot ask codex-lb to refresh and report what happened.

## What Changes

- Add API-key-authenticated `GET /api/fleet/summary` for minimal per-account capacity state.
- Add API-key-authenticated `POST /api/fleet/refresh` that runs the existing usage refresh path outside proxy request selection.
- Return a minimal non-sensitive refresh outcome with `usageWritten`, `accountCount`, `attemptedCount`, and `generatedAt`.
- Preserve existing usage-refresh freshness, cooldown, paused, reauth-required, and deactivated-account rules.

## Capabilities

### New Capabilities

- `fleet-summary`: expose minimal account capacity for trusted local fleet consumers and let them request bounded refresh attempts.

## Impact

- **Backend**: new `app/modules/fleet` API, schemas, and mappers; router registration in `app/main.py`.
- **Specs**: new `fleet-summary` capability.
- **Tests**: integration coverage for auth, minimal projection, sensitive-field omission, and refresh outcome shape.
- **No database migration**: no schema changes.
