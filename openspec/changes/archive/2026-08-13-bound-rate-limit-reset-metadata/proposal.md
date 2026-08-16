## Why

An OpenAI service rate-limit error can carry malformed or wrong-unit reset metadata, and codex-lb currently persists that value without validation. An implausible deadline can therefore keep an account `rate_limited` indefinitely even after fresh usage proves quota is available.

## What Changes

- Validate absolute and relative OpenAI service reset metadata against a conservative finite future horizon before using it as a cooldown deadline.
- Fall back to the existing Retry-After or bounded backoff path when reset metadata is non-finite, elapsed, or implausibly far in the future.
- Treat already-persisted implausible rate-limit deadlines as invalid so normal selection and background usage recovery can repair affected accounts.
- Add regressions for malformed metadata and automatic recovery of an affected persisted account.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Bound explicit OpenAI service reset metadata and define the fallback and persisted-row behavior for implausible deadlines.
- `usage-refresh-policy`: Allow fresh post-block quota evidence to recover accounts whose persisted rate-limit deadline is implausible.

## Impact

- Affected code: balancer rate-limit handling, account selection state derivation, and usage-refresh recovery.
- Affected data: existing `accounts.reset_at` values are interpreted defensively; no schema migration or bulk rewrite is required.
- APIs and configuration: no API, environment variable, or dashboard changes.
