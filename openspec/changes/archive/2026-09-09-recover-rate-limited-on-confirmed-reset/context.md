## Warm-up settings lifecycle

This change originally made reset-confirmed warm-up independent of
`limit_warmup_exhausted_threshold_percent`. The active
`restore-limit-warmup-threshold` change supersedes that eligibility decision:
the existing public setting now gates pre-reset usage, with `0%` preserving the
all-resets behavior and positive values requiring the configured floor.

The reset-confirmed warm-up requirement, including this change's
recovery-before-warm-up and active-account safeguards, now lives in that
superseding change so the two active deltas cannot overwrite each other with
opposite contracts when synced or archived.

`limit_warmup_cooldown_seconds` remains active but intentionally applies only to staggered idle warm-up. Reset-confirmed warm-up uses the atomic account/window/reset claim instead: an attempt for the same tuple is deduplicated, while a distinct real reset is not suppressed merely because another attempt happened recently.
