## Why

Current recovery already rejects fresh evidence while an applicable usage
window remains exhausted. However, that gate can still keep an account blocked
when the exhausted long window is covered by usable credits, or when a
zero-capacity plan retains a synthetic exhausted primary row. Those rows do not
mean the account is unable to serve the request.

## What Changes

- Make the existing all-window recovery gate credit-aware.
- Ignore synthetic primary usage only when the canonical plan has zero primary
  capacity.
- Preserve the existing expiry, credit override, and active-account routing
  contracts; do not turn advisory usage into new account blocks.
- Keep plan-alias resolution out of this change.
- Cover credit-backed and synthetic-primary recovery plus repeated sticky HTTP
  requests.

## Impact

- Account-state recovery and regression tests only.
- No database migrations, settings, credentials, or wire-protocol changes.
