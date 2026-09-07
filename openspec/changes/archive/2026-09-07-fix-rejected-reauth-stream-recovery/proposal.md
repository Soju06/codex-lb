## Why

A native HTTP Responses request can receive `401 token_expired`, fail forced refresh, and then both require and exclude its first dispatch account. The client receives `preferred_account_unavailable`, and independent messages select the same unusable reauthentication-warning account again when JWT expiry is unknown or misleading.

## What Changes

- Persist the existing `account_auth_invalidated` reason when a streaming access-token rejection cannot be repaired by forced refresh, and exclude that state from new selection and live bridge reuse.
- Guard the status write against both credential ciphertexts so stale failures cannot undo a concurrent repair.
- Retry only pre-visible requests whose replacement body passes the existing account-neutral replay checks; retain independent hard ownership and opaque input.
- Preserve the authentication failure when no legal replacement is available, instead of surfacing a contradictory preferred-account selection error.
- Keep refresh-token-only warnings routable while their access token remains usable.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Exclude proven rejected reauthentication accounts until credential repair, including cross-replica routing snapshots.
- `responses-api-compat`: Recover pre-visible HTTP authentication failures without weakening payload ownership or reservation settlement.

## Impact

Streaming retry, account eligibility, routing availability cache, and guarded account-status persistence. No new configuration, database column, dependency, dashboard surface, or deployment action. This complements PR #2117's direct `token_revoked` handling and PR #2001's HTTP 429 dispatch-ownership fix; it does not include either PR.
