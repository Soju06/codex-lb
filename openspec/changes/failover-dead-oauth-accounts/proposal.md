## Why

Upstream can reject an access token with `token_revoked`. If the subsequent
forced refresh also proves the account credentials permanently unusable, the
compact path currently surfaces that account-local 401 even when another
account can safely serve the request.

## What Changes

- Recognize `token_revoked` as a permanent reauthentication failure.
- Exclude a dead account from the remaining attempts of a movable compact
  request after its forced refresh fails permanently.
- Preserve fail-closed behavior for compact requests bound to an account owner.
- Preserve API-key settlement before account-health mutation.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: Recover movable compact work from revoked accounts.
- `account-routing`: Classify the upstream revoked-token spelling as requiring
  reauthentication.

## Impact

- Compact auth recovery and shared permanent-failure classification.
- Compact, WebSocket, and upstream error status regression coverage.
