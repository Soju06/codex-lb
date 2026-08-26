# Treat Invalid Refresh Token as Reauth

## Why

Upstream OAuth can reject a stored refresh token with `invalid_refresh_token`.
Leaving that code outside the permanent failure set keeps the account active even
though every fresh upstream attempt will retry dead credentials.

## What Changes

- Classify `invalid_refresh_token` as a permanent refresh failure.
- Persist affected accounts as re-authentication required through the guarded
  refresh-account status path.
- Keep those accounts excluded from normal routing until an operator reauthenticates
  or imports fresh credentials.

## Impact

- Affected specs: `account-routing`
- Affected code: `app/core/auth/refresh.py`, `app/core/balancer/logic.py`,
  `app/modules/accounts/auth_manager.py`
