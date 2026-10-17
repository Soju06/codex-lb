## Why

An account imported while it is already on the Free plan writes its first
monthly usage sample before the operator can enable per-account limit warm-up.
After the opt-in is enabled, ordinary reset detection correctly rejects the
sliding monthly `reset_at` as not being a confirmed reset, while the paid-to-
Free fallback does not apply because the account was already Free. The result
is an opted-in, completely unused Free quota that can remain without any
warm-up attempt indefinitely.

## What Changes

- Treat the first eligible refresh of an already-Free, fully unused monthly
  quota as a one-time initial warm-up candidate when the account has no prior
  warm-up attempt.
- Require the monthly sample to be written by the current refresh and preserve
  the existing active-account, global opt-in, per-account opt-in, selected
  long-window, and model-availability gates.
- Extend the existing account/monthly/reset atomic claim with an optional
  account-wide no-prior-attempt guard and leave ordinary reset confirmation
  unchanged.
- Add regressions for the consumer-visible attempt and the stale, partially
  used, and previously attempted boundaries.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Allow one initial monthly warm-up for an opted-in,
  unused account that was already Free when imported.

## Impact

- Affected code: limit warm-up service/repository and the background repository
  adapter.
- Affected tests: focused limit warm-up unit and atomic-claim integration
  tests.
- No API, schema, migration, setting, dependency, dashboard, or deployment
  change.
