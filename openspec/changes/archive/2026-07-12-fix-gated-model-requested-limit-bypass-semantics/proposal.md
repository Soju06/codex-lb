## Why

The proxy now supports routing gated models by a requested or derived additional
quota, including `QUOTA_EXCEEDED` bypass and requested-limit usage weighting.
Current routing behavior has to keep two invariants clear:

- requested-limit ranking must never mix additional-quota signals with ordinary
  account usage windows when those additional windows are missing.
- `QUOTA_EXCEEDED` cooldown semantics must remain enforced even when
  additional-quota bypass is used.

## What Changes

- Extend load-balancer state handling so requested-limit secondary quota pressure is
  applied only when a requested secondary window exists; requested-limit primary
  pressure remains the priority input.
- Keep requested-limit selection from bypassing `cooldown_until` in
  `select_account`, so cooldown backoff stays in effect after quota errors.
- Preserve behavior for regular non-gated selection paths.

## Impact

- Gated/requested-limit routing follows documented request-window behavior without
  borrowing ordinary secondary usage pressure.
- Cooldown protection remains in force for `QUOTA_EXCEEDED` accounts even when
  requested-quota bypass is active.
- Existing selection fallback and tests remain aligned with the stronger
  backoff/selection semantics.
