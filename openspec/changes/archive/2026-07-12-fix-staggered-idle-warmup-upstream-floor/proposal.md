## Why

The staggered idle warm-up feature (PR #905, issue #433) is effectively dead
code: it never fires in practice because the idle gate requires
`used_percent == 0.0`, but the upstream ChatGPT API reports a **1.0% floor**
for idle primary 5h windows. Across 2,460+ recorded primary usage entries,
zero have `used_percent == 0.0` — the minimum is always 1.0%.

Additionally, the dashboard's "Exhausted at %" setting
(`limit_warmup_exhausted_threshold_percent`) was only wired to the regular
(post-exhaustion) warm-up path, not to the staggered idle path. The UI placed
this field in the shared warm-up settings grid beneath the staggered idle
toggle, so operators reasonably expected it to control both warm-up modes.

A third bug was discovered during testing: the reset-confirmed warm-up's
`reset_at > before.reset_at` comparison was too sensitive — upstream
`reset_at` values can fluctuate by ~1 second between refresh cycles, causing
false warm-up triggers mid-window when "Min usage %" was set to a low value.

## What Changes

- Add a new separate `limit_warmup_idle_threshold_percent` setting (default
  1.0) wired to the staggered idle path. The existing
  `limit_warmup_exhausted_threshold_percent` (default 99.0) remains scoped to
  the regular warm-up path.
- Change the staggered idle gate from hardcoded `used_percent > 0.0` to
  `used_percent > idle_threshold_percent`, so accounts at the upstream idle
  floor (1.0%) now qualify.
- Require a minimum 60-second forward `reset_at` jump to confirm a real quota
  window reset, preventing upstream timestamp jitter from triggering false
  warm-ups.
- Redesign the warm-up settings UI into two side-by-side cards:
  "Reset-confirmed warm-up" (with "Min usage %") and "Staggered idle warm-up"
  (with "Max usage %"), clearly separating the two modes and their respective
  thresholds.

## Impact

- Staggered idle warm-up will now actually fire for idle accounts during the
  account's deterministic slot in the 5h window, as originally intended by
  issue #433.
- The "Min usage %" and "Max usage %" dashboard settings now clearly control
  separate warm-up modes.
- Reset-confirmed warm-up no longer fires on upstream timestamp jitter.
- Database migration adds the new `limit_warmup_idle_threshold_percent` column.
