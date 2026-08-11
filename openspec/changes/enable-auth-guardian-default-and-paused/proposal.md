# Enable Auth Guardian by default and cover paused accounts

## Why

Operators with many linked accounts report refresh tokens silently expiring on
rarely used accounts. The Auth Guardian scheduler (added in
`add-auth-guardian-refresh`) already solves this, but two gaps keep the
protection from actually reaching the accounts that need it most:

1. `CODEX_LB_AUTH_GUARDIAN_ENABLED` defaults to `False`, so the base install
   has no proactive keepalive at all. The guardian is invisible (no dashboard
   surface), so most operators never learn the switch exists until a token has
   already expired.
2. The candidate filter only admits `active` accounts. Pausing an account is
   the natural way to shelve a rarely used account, and it is exactly the
   account whose refresh token will rot — paused accounts receive no traffic,
   never hit the reactive 401 refresh path, and are skipped by the usage
   refresh scheduler.

## What Changes

- `auth_guardian_enabled` setting default flips `False` -> `True`. The
  existing multi-replica leader guard is unchanged: multi-replica rings
  without leader election still self-disable with a warning, so the flipped
  default cannot introduce concurrent force-refreshes.
- Auth Guardian candidate selection admits `paused` accounts in addition to
  `active` ones. `reauth_required` and `deactivated` stay excluded (their
  refresh tokens are known-bad or intentionally retired). Credential refresh
  does not change a paused account's routing eligibility: it stays out of
  request routing. A permanent refresh failure still transitions the account
  to its documented permanent-failure status exactly as it does for active
  accounts today.

## Impact

- Affected specs: `usage-refresh-policy` (proactive credential refresh
  requirement).
- Affected code: `app/core/config/settings.py`,
  `app/core/auth/guardian.py`, `docs/reference/settings.md`, unit and
  settings-default tests.
- Base-install behavior change: background OAuth token refresh runs by
  default. Bounded by existing constants (interval 6h, staleness gate 12h,
  batch 100, concurrency 3, jitter, per-account failure backoff) and
  leader-gated, so upstream load is negligible and strictly bounded.
- Simplicity gates: no new setting, no new required setup step. The flip
  makes the feature a zero-config working default (P1); the existing
  `CODEX_LB_AUTH_GUARDIAN_ENABLED=false` opt-out is preserved.
