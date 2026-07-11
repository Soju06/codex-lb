## Why

The staggered idle warm-up feature (PR #905, issue #433) is effectively dead
code: it never fires in practice because the idle gate requires
`used_percent == 0.0`, but the upstream ChatGPT API reports a **1.0% floor**
for idle primary 5h windows. Across 2,460+ recorded primary usage entries,
zero have `used_percent == 0.0` — the minimum is always 1.0%.

This means the "pre-start rolling 5h window" feature that operators enable
via the "Stagger idle warm-up" toggle can never trigger, and the dashboard
always shows "No attempts" for staggered idle warm-up regardless of how long
the account sits idle.

Additionally, the dashboard's "Exhausted at %" setting
(`limit_warmup_exhausted_threshold_percent`) was only wired to the regular
(post-exhaustion) warm-up path. The UI places this field in the shared
warm-up settings grid beneath the staggered idle toggle, so operators
reasonably expect it to control both warm-up modes. Reusing the same setting
for both paths is problematic because the default of 99.0% means "exhausted"
for the regular path but would mean "almost everything is idle" for the
staggered path — opposite semantics.

## What Changes

- Add a new `limit_warmup_idle_threshold_percent` setting (default 1.0) that
  controls the staggered idle gate independently from the regular warm-up's
  exhaustion threshold.
- Wire the new setting into `_build_staggered_idle_candidate`; an account with
  `used_percent` at or below the configured idle threshold is considered idle.
- Add an Alembic migration for the new `dashboard_settings` column.
- Add a new "Idle at %" input to the dashboard settings UI, shown beneath the
  staggered idle toggle when that mode is enabled.
- The existing "Exhausted at %" field remains scoped to the regular warm-up
  path only.

## Impact

- Staggered idle warm-up will now actually fire for idle accounts during the
  account's deterministic slot in the 5h window, as originally intended by
  issue #433.
- Operators can independently configure the idle threshold (default 1.0%
  matching the upstream floor) and the exhaustion threshold (default 99.0%).
- Database migration adds a new column; existing deployments get the default
  of 1.0 automatically.
- No change to the regular (post-exhaustion) warm-up path logic.
