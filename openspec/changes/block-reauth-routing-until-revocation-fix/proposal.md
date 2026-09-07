## Why

An upstream access token can be revoked before its JWT expiry. Since `reauth_required` accounts were made request-routable, those accounts repeatedly re-enter proxy selection, return `401 token_revoked`, and leave affected sticky sessions failing until re-authentication.

## What Changes

- Temporarily restore fail-closed proxy routing for every `reauth_required` account, regardless of the stored access token's nominal expiry.
- Prevent new selection and live HTTP bridge reuse for those accounts while preserving hard continuity as unavailable rather than moving it across accounts.
- Keep re-authentication as the recovery path; add no setting, schema, migration, or new persisted credential state.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Clarify that the temporary safety policy excludes `reauth_required` accounts from proxy selection and bridge reuse until re-authentication.

## Impact

Proxy account eligibility, load-balancer selection, HTTP bridge reuse, and their regression tests are affected. Capacity from warning-state accounts is intentionally unavailable until an upstream fix can distinguish a bad refresh token from a revoked access token.
