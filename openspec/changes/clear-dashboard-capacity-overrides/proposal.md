# Clear dashboard account-capacity overrides

## Why

Dashboard settings can override the per-account concurrency limits, but an
operator cannot remove an existing override and return to the environment
value. The update path currently uses `None` for both an omitted field and an
explicit clear request, so a stored override remains in place. Setting the
current environment value manually is not equivalent: later environment
changes still do not take effect.

This resolves issue #1538 without changing the runtime admission model.

## What Changes

- Preserve the existing effective settings fields, expose environment baseline
  values for prospective validation, and add nullable raw
  override fields for the four dashboard-managed capacity values:
  `proxy_account_response_create_limit`, `proxy_account_stream_limit`,
  `proxy_account_stream_recovery_reserve`, and
  `proxy_api_key_fair_share_congestion_threshold_pct`.
- Define tri-state update semantics: an absent field remains unchanged, a
  numeric value stores an override, and an explicit `null` clears the stored
  override so the environment value becomes effective.
- Thread the clear operation through the settings API, service, repository,
  settings cache invalidation, and audit changed-field reporting.
- Let routing settings display the raw override when present and an empty
  inheritable input with the effective environment value as its hint when the
  override is `NULL`.
- Reuse the existing nullable database columns; no schema migration or
  runtime admission behavior changes are required.

## Impact

- Existing effective-value API fields remain backward compatible.
- Existing clients that omit capacity fields retain their current behavior.
- An explicit `null` becomes meaningful only for the four capacity override
  fields.
- Clearing an override takes effect through the existing settings cache
  invalidation path without a process restart.

## Non-goals

- No changes to account selection, stream admission, or capacity arithmetic.
- No new environment variables or dashboard navigation.
- No database migration or backfill.
- No change to the semantics of unrelated nullable settings.
