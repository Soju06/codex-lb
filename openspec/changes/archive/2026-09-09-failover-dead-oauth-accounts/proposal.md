## Why

Upstream can reject an access token with `token_revoked`, but shared account
health and proxy error classification do not consistently recognize that spelling
as equivalent to `token_invalidated`.

Compact forced-refresh failover is already implemented by
`fail-over-compact-permanent-refresh` (#2080). This change only adds the revoked
token classification and verifies it against those existing recovery contracts.

## What Changes

- Recognize `token_revoked` as a permanent reauthentication failure.
- Preserve HTTP 401 mapping even when upstream omits the authentication error type.
- Apply the same HTTP 401 mapping to non-streaming Images requests and their
  Codex-base aliases after failover is exhausted.
- Use the shared WebSocket authentication-failure code set for `token_revoked`.
- Make the existing HTTP bridge file-affinity ordering test hermetic without
  changing its fail-closed assertion.

## Capabilities

### Modified Capabilities

- `account-routing`: Classify the upstream revoked-token spelling as requiring
  reauthentication.
- `images-api-compat`: Preserve HTTP 401 for revoked-token terminal errors.

## Impact

- Shared permanent-failure classification and proxy error status mapping.
- Regression coverage for revoked-token handling, existing compact recovery,
  account ownership, settlement ordering, and HTTP bridge file-affinity ordering.
- No changes to the compact implementation or its OpenSpec requirement from #2080.
