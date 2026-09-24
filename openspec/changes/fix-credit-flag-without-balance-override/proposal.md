## Why

Some paid accounts report `credits.has_credits = true` after the applicable long quota window is exhausted while also reporting no spendable balance. codex-lb currently treats that bare flag as enough to keep the account `active`, so an exhausted account can stay routable and fail repeated proxy requests.

## What Changes

- Require positive spendable-credit evidence before credit metadata overrides exhausted long-window usage.
- Keep `credits_unlimited = true` and `credits_balance > 0` as valid overrides.
- Treat `credits_has = true` with missing or zero balance as non-spendable for quota/status derivation.
- Preserve primary-window `rate_limited` precedence and later recovery when a newer snapshot reports unlimited credits or a positive balance.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `usage-refresh-policy`: tighten credit-backed quota override semantics for account status derivation.

## Impact

- Affects usage-quota derivation, account-summary status mapping, and proxy account-state derivation.
- No schema, migration, API, setting, or dashboard layout change.
