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
(post-exhaustion) warm-up path (`_build_candidate`), not to the staggered
idle path. The UI places this field in the shared warm-up settings grid
beneath the staggered idle toggle, so operators reasonably expect it to
control both warm-up modes. An operator lowering the threshold to make
staggered idle fire for accounts below a certain usage level had no effect.

## What Changes

- Wire the existing `limit_warmup_exhausted_threshold_percent` setting into
  the staggered idle path (`_build_staggered_idle_candidate`), so the
  "Exhausted at %" field in the dashboard controls the idle gate for both
  warm-up modes.
- The staggered idle gate changes from the hardcoded `used_percent > 0.0`
  to `used_percent > idle_threshold_percent`, where `idle_threshold_percent`
  is the configured `limit_warmup_exhausted_threshold_percent` (default 99.0).
- With the default threshold of 99.0%, accounts at or below 99% used are
  considered idle for staggered warm-up. Operators can lower this to be more
  conservative (e.g. 1.0 to only warm truly idle accounts at the upstream
  floor) or raise it toward 99.0 for broader coverage.
- Add a regression test asserting that an account at `used_percent = 1.0`
  with the threshold set to 1.0 qualifies for staggered idle warm-up.

## Impact

- Staggered idle warm-up will now actually fire for idle accounts during the
  account's deterministic slot in the 5h window, as originally intended by
  issue #433.
- The "Exhausted at %" dashboard setting now controls both the regular
  warm-up exhaustion gate and the staggered idle idle gate.
- No change to the regular (post-exhaustion) warm-up path logic.
- No database migration — reuses the existing
  `limit_warmup_exhausted_threshold_percent` column.
