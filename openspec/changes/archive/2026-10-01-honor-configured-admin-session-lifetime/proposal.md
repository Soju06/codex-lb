## Why

An explicitly configured dashboard password-session lifetime between 12 hours
and 30 days is honored for operators but silently shortened for remote admins.
This prevents an operator from choosing a longer login duration for their own
admin account, including access through a private HTTPS reverse proxy.

## What Changes

- Apply the existing configurable password-session lifetime to admin accounts,
  including remote and reverse-proxied requests.
- Retain the existing local-access requirements for lifetimes above 30 days and
  the 12-hour remote fallback for those values.
- Preserve absolute expiry, existing cookies, account session revocation, TOTP,
  and the independent 12-hour OIDC session limit.
- Reuse the persisted setting; add no environment variable, schema migration,
  authentication bypass, or proxy-trust exception.

## Capabilities

### Modified Capabilities

- `admin-auth`: honor configured password-session lifetimes up to 30 days
  regardless of the account's role.

## Impact

Only password-session lifetime resolution and its tests change. Deployments
using the default one-year setting still receive 12-hour remote sessions.
Admins who explicitly chose a duration between 12 hours and 30 days receive
that duration after their next password login.
