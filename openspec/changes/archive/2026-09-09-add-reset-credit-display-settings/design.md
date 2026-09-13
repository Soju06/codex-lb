## Overview

Add reset-credit display preferences to dashboard settings and extend the reset-credit refresh scheduler with an optional automatic redemption pass. The automatic pass must call the existing redeem helper so it inherits credit selection, durable idempotency pins, cross-replica serialization, cache invalidation, and forced usage refresh behavior.

## Decisions

- Persist the three new switches in `dashboard_settings` because they are shown in Settings and affect both UI surfaces and scheduler behavior.
- Default `show_reset_credit_badges` and `show_reset_credit_expiry_badge` to `true` to preserve the current UI.
- Default `auto_redeem_reset_credits_before_expiry` to `false` because there is currently no automatic redeem behavior.
- Use the existing reset-credit scheduler as the automatic redeem driver. It already has eligible account iteration, token decrypt, route resolution, snapshot refresh, per-account error isolation, and cadence/jitter behavior.
- Use `_redeem_soonest_reset_credit` from `app.modules.rate_limit_reset_credits.api` for automatic redeem. This avoids duplicating selection, consume, ledger, locking, invalidation, and usage refresh logic.
- Keep automatic refresh and consume route resolution distinct: the polling fetch uses the existing usage-refresh route, while the automatic redeem helper receives the reset-credit consume route.
- The automatic redemption window is five minutes. After a snapshot refresh, if the soonest available credit expires within five minutes, the scheduler attempts to redeem it.
- Automatic redemption uses a stable request id per account and UTC expiry date, not per scheduler tick. If an automatic request for that account/expiry date already has a durable pin, the scheduler skips instead of retrying a second upstream consume. The redeem helper also enforces this skip inside its serialized section so concurrent replicas that race before the first pin still do not issue a second automatic consume after the pin exists.

## Data Model

Add boolean columns to `dashboard_settings`:

- `show_reset_credit_badges`, default true
- `auto_redeem_reset_credits_before_expiry`, default false
- `show_reset_credit_expiry_badge`, default true

Expose the fields through backend Pydantic schemas and frontend Zod schemas using existing snake_case to camelCase mapping.

## Frontend

Add a compact Reset credits settings section near other always-visible settings. Use switches only; no explanatory in-app walkthrough text beyond labels/descriptions consistent with existing settings sections.

Read `showResetCreditBadges` in:

- `AppHeader` to hide/show top-nav total reset-credit count
- `AccountListItem` via prop from `AccountList`/`AccountsPage`

Read `showResetCreditExpiryBadge` in:

- `AccountActions` via prop from `AccountDetail`/`AccountsPage`

Show nearest reset-credit expiry in `AccountUsagePanel` using `account.resetCreditNearestExpiresAt`, with local datetime formatting and `formatSingleUnitRemaining`.

## Scheduler

`RateLimitResetCreditsRefreshScheduler._refresh_once()` reads dashboard settings once per tick. The account read session remains closed before any upstream fetch/redeem. The refresh function accepts `auto_redeem_before_expiry` and `auto_redeem_window_seconds`; when enabled and a refreshed snapshot's nearest available expiry is within the five-minute window, it calls the existing redeem helper for that account.

Automatic redemption uses background sessions only inside the existing helper path as required by the redeem ledger/serializer helpers. Each account failure remains isolated and logged. Duplicate automatic attempts for a pinned automatic request are treated as no-ops, not warnings.

## Verification

Automatic redemption must be covered with mocked/stubbed tests that exercise the scheduler-to-redeem-helper wiring without contacting upstream or performing a live redeem. Other touched settings/API/UI behavior should have focused regression coverage and relevant test suites should be run, along with OpenSpec validation and code-path inspection.
