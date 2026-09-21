## Why

Automatic reset-credit redemption can miss an expiry while scanning the account list, and a durable request pin currently suppresses recovery even when the consume never succeeded. Redeeming one account also clears unrelated reset-credit snapshots on peer replicas and triggers broad dashboard refetches, making other accounts' Reset buttons disappear temporarily.

## What Changes

- Persist redemption outcomes separately from request pins; reconcile ambiguous responses and retry only the same credit with the same request id.
- Schedule known expiries independently of bulk discovery, with bounded concurrency and a one-hour redemption window in place of the current five-minute window. Preserve the existing opt-in setting and its disabled default.
- Require the exact triggering credit id and expiry at consume time. Never substitute a later credit when the original expires or disappears.
- Record automatic and manual outcomes, distinguish confirmed redemption from expiry or missing evidence, and refresh quota without issuing another consume when quota visibility lags.
- Invalidate only the affected account on all updated replicas and update that account in the dashboard without refetching the entire account list.
- Deliver in three ordered implementation slices: outcome safety; deadline scheduling; scoped cache and dashboard updates.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `rate-limit-reset-credits`: durable outcomes and recovery, exact-credit deadline scheduling, and account-scoped cross-replica invalidation.
- `frontend-architecture`: targeted post-redeem updates, truthful pending/failure states, and settings copy matching the one-hour window.

## Impact

- Backend: reset-credit scheduler, consume helper/routes, coordination ledger, snapshot store, cache-invalidation wiring, account summary reads, audit records, and usage refresh.
- Persistence: additive ledger outcome fields and per-account invalidation revisions, with historical-row compatibility and PostgreSQL/SQLite migration coverage.
- Frontend: account query hooks, reset-credit confirmation/result handling, dashboard account projections, and reset-credit settings text.
- API: preserve existing consume response fields, authentication, canonical/alias paths, and native error envelopes; add a dashboard-only targeted account-summary read for reconciliation.
- No new environment variables, dependencies, or navigation entries. Application changes are implemented locally; production auto-redeem configuration is unchanged and deployment remains a separate operator action.
