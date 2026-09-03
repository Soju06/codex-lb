## Why

The usage refresh worker currently treats every HTTP 404 as proof that an account is
permanently deactivated. An upstream endpoint, routing, or rollout failure can return the
same bare 404 for many healthy accounts, which incorrectly removes the whole account pool and
forces unnecessary re-login.

## What Changes

- Keep the existing payment-required and explicit account-deactivation signals as permanent.
- Treat a bare or otherwise ambiguous usage HTTP 404 as a transient refresh failure.
- Preserve the account's credentials and serving eligibility when the upstream provides no
  unambiguous account-level deactivation signal.

## Impact

Only usage-refresh error classification and its regression coverage change. No schema migration
or credential migration is required; accounts already affected by the incident can be restored
by an operator after verifying their stored token material is unchanged.
